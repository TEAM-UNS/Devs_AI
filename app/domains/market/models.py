"""market 스키마가 소유하는 테이블 (schema="market").

DDL 원본은 루트 init.sql. 이 파일은 그와 1:1로 대응해야 한다.
(`alembic revision --autogenerate` 가 빈 마이그레이션을 뱉으면 일치한다)

    tech_field       분야 분류
    company          기업. name_key 로 사이트 간 통합. profile_embedding
    company_source   사이트별 기업 식별자 (오병합 추적)
    job_posting      공고. content_hash(변경감지) · embed_hash(재임베딩 판단)
    posting_skill    공고 ↔ 스킬
    posting_chunk    임베딩 단위
    skill / skill_alias / skill_field
    crawl_run        수집·임베딩 실행 이력

규칙
    - 이 모듈은 다른 도메인을 import 하지 않는다 (R1)
    - CHECK 제약 문자열은 core/enums.py 에서 생성한다. Enum 이 곧 DDL 이다
    - HNSW · 부분 · GIN 인덱스는 __table_args__ 에 명시한다
    - updated_at 갱신은 DB 트리거(public.touch_updated_at)가 담당한다
    - ★ `from __future__ import annotations` 를 쓰지 않는다.
      모든 어노테이션이 문자열이 되면 SQLModel 이 Relationship 대상 클래스를
      해석하지 못해 mapper 초기화에서 터진다 (SQLModel 의 알려진 제약).
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

from app.core import enums
from app.core.enums import EMBEDDING_DIM

SCHEMA = "market"

# 벡터 인덱스 파라미터. 수집량이 크게 늘면 재조정 + REINDEX 를 검토한다.
_HNSW = {"m": 16, "ef_construction": 64}

# ★ Enum 컬럼은 반드시 sa_type=String(n) 으로 고정한다.
#   그냥 두면 SQLModel 이 네이티브 PG ENUM 타입을 만들어 버린다. 그러면
#   (1) init.sql 의 varchar + CHECK 와 어긋나고
#   (2) 값을 하나 추가할 때마다 ALTER TYPE 마이그레이션이 필요해진다.
#   값 검증은 CHECK 제약(enums.sql_in)이 담당한다.
#   같은 이유로 긴 본문은 sa_type=Text 로 고정한다(기본값은 VARCHAR).


def _created_at() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Column:
    # 갱신은 트리거가 한다. 여기서는 생성 시각만 채운다.
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ═══════════════════════════════════════════════════════════════════════════
#  분류 체계
# ═══════════════════════════════════════════════════════════════════════════
class TechField(SQLModel, table=True):
    """기술 분야. code 는 enums.TechField 와 1:1."""

    __tablename__ = "tech_field"
    __table_args__ = {"schema": SCHEMA}

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(max_length=32, unique=True)
    name: str = Field(max_length=64)
    sort_order: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    created_at: datetime | None = Field(default=None, sa_column=_created_at())

    postings: list["JobPosting"] = Relationship(back_populates="field")


# ═══════════════════════════════════════════════════════════════════════════
#  기업
# ═══════════════════════════════════════════════════════════════════════════
class Company(SQLModel, table=True):
    __tablename__ = "company"
    __table_args__ = (
        CheckConstraint(enums.sql_in("size_type", enums.CompanySize), name="company_size_type_chk"),
        CheckConstraint(
            "employee_count IS NULL OR employee_count >= 0",
            name="company_employee_count_chk",
        ),
        Index(
            "company_name_trgm_idx",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("company_size_type_idx", "size_type"),
        # 프로필 임베딩 미완료분 백필용
        Index(
            "company_embed_todo_idx",
            "id",
            postgresql_where=text("profile_embedding IS NULL"),
        ),
        Index(
            "company_profile_embedding_idx",
            "profile_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"profile_embedding": "vector_cosine_ops"},
            postgresql_with=_HNSW,
        ),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)

    # 괄호 · "주식회사" · 공백을 제거한 정규화 이름. 사이트 간 통합의 기준.
    name_key: str = Field(max_length=200, unique=True)
    name: str = Field(max_length=200)

    description: str | None = Field(default=None, sa_type=Text)  # 기업 소개
    business_content: str | None = Field(default=None, sa_type=Text)  # 사업 내용
    talent_profile: str | None = Field(default=None, sa_type=Text)  # 인재상

    size_type: enums.CompanySize = Field(
        default=enums.CompanySize.UNKNOWN,
        sa_type=String(20),
        sa_column_kwargs={"server_default": text("'unknown'")},
    )
    employee_count: int | None = None
    industry: str | None = Field(default=None, max_length=120)
    founded: str | None = Field(default=None, max_length=20)  # 표기가 제각각이라 문자열
    revenue: int | None = Field(default=None, sa_type=BigInteger)  # 원 단위
    homepage: str | None = Field(default=None, max_length=500)

    # description + business_content + industry 를 합쳐 임베딩
    profile_embedding: list[float] | None = Field(
        default=None, sa_column=Column(Vector(EMBEDDING_DIM))
    )
    embed_hash: str | None = Field(default=None, sa_column=Column(CHAR(64)))

    raw_fields: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=_created_at())
    updated_at: datetime | None = Field(default=None, sa_column=_updated_at())

    sources: list["CompanySource"] = Relationship(back_populates="company", cascade_delete=True)
    postings: list["JobPosting"] = Relationship(back_populates="company")


class CompanySource(SQLModel, table=True):
    """같은 기업이 사이트마다 갖는 다른 식별자. 오병합 추적용 원장."""

    __tablename__ = "company_source"
    __table_args__ = (
        CheckConstraint(
            enums.sql_in("source", enums.CrawlSource), name="company_source_source_chk"
        ),
        UniqueConstraint("source", "source_company_id", name="company_source_uk"),
        Index("company_source_company_idx", "company_id"),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    company_id: int = Field(
        sa_type=BigInteger, foreign_key=f"{SCHEMA}.company.id", ondelete="CASCADE"
    )
    source: enums.CrawlSource = Field(sa_type=String(20))
    source_company_id: str = Field(max_length=100)
    url: str | None = Field(default=None, max_length=500)
    collected_at: datetime | None = Field(default=None, sa_column=_created_at())

    company: Company | None = Relationship(back_populates="sources")


# ═══════════════════════════════════════════════════════════════════════════
#  공고
# ═══════════════════════════════════════════════════════════════════════════
class JobPosting(SQLModel, table=True):
    __tablename__ = "job_posting"
    __table_args__ = (
        CheckConstraint(enums.sql_in("source", enums.CrawlSource), name="job_posting_source_chk"),
        CheckConstraint(
            enums.sql_in("salary_type", enums.SalaryType),
            name="job_posting_salary_type_chk",
        ),
        CheckConstraint(
            "career_min IS NULL OR career_max IS NULL OR career_min <= career_max",
            name="job_posting_career_chk",
        ),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max",
            name="job_posting_salary_chk",
        ),
        UniqueConstraint("source", "source_job_id", name="job_posting_uk"),
        Index("job_posting_company_idx", "company_id"),
        Index("job_posting_field_posted_idx", "field_id", text("posted_at DESC")),
        Index("job_posting_posted_idx", text("posted_at DESC")),
        Index("job_posting_collected_idx", text("collected_at DESC")),
        Index("job_posting_career_idx", "career_min", "career_max"),
        Index("job_posting_location_idx", "location"),
        # 연봉 통계 대상(금액 공개 공고)만 좁게 태우는 부분 인덱스
        Index(
            "job_posting_salary_idx",
            "field_id",
            "salary_min",
            "salary_max",
            postgresql_where=text("salary_type IN ('range', 'min_only', 'max_only')"),
        ),
        # embed_backfill 대상 스캔용
        Index(
            "job_posting_embed_todo_idx",
            "id",
            postgresql_where=text(
                "body_is_image = false AND description IS NOT NULL "
                "AND (embed_hash IS NULL OR embed_hash IS DISTINCT FROM content_hash)"
            ),
        ),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)

    source: enums.CrawlSource = Field(sa_type=String(20))
    source_job_id: str = Field(max_length=100)

    company_id: int | None = Field(
        default=None,
        sa_type=BigInteger,
        foreign_key=f"{SCHEMA}.company.id",
        ondelete="SET NULL",
    )
    field_id: int | None = Field(
        default=None, foreign_key=f"{SCHEMA}.tech_field.id", ondelete="SET NULL"
    )

    title: str = Field(max_length=300)
    career_min: int | None = None  # 신입 = 0, 무관 = None
    career_max: int | None = None
    employment_type: str | None = Field(default=None, max_length=30)
    education: str | None = Field(default=None, max_length=30)
    location: str | None = Field(default=None, max_length=120)

    # 요강 전문. 3중 폴백 전부 실패 시 None
    description: str | None = Field(default=None, sa_type=Text)
    welfare: str | None = Field(default=None, sa_type=Text)

    salary_raw: str | None = Field(default=None, sa_type=Text)
    salary_min: int | None = None  # 만원 단위
    salary_max: int | None = None
    salary_type: enums.SalaryType = Field(
        default=enums.SalaryType.UNKNOWN,
        sa_type=String(16),
        sa_column_kwargs={"server_default": text("'unknown'")},
    )

    # 본문 200자 미만 + 이미지 존재. 집계·임베딩 대상에서 제외한다.
    body_is_image: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    posted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    content_hash: str | None = Field(default=None, sa_column=Column(CHAR(64)))
    embed_hash: str | None = Field(default=None, sa_column=Column(CHAR(64)))

    collected_at: datetime | None = Field(default=None, sa_column=_created_at())
    raw_fields: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=_created_at())
    updated_at: datetime | None = Field(default=None, sa_column=_updated_at())

    company: Company | None = Relationship(back_populates="postings")
    field: TechField | None = Relationship(back_populates="postings")
    skills: list["PostingSkill"] = Relationship(back_populates="posting", cascade_delete=True)
    chunks: list["PostingChunk"] = Relationship(back_populates="posting", cascade_delete=True)


# ═══════════════════════════════════════════════════════════════════════════
#  스킬
# ═══════════════════════════════════════════════════════════════════════════
class Skill(SQLModel, table=True):
    __tablename__ = "skill"
    __table_args__ = {"schema": SCHEMA}

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=80, unique=True)  # 정규화 표기 ("Spring Boot")
    category: str | None = Field(default=None, max_length=32)
    # Go · C · R — 문맥 단서 없으면 미채택
    is_ambiguous: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    # name + aliases. 200행 규모라 앱 시작 시 메모리로 로드한다(인덱스 없음).
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector(EMBEDDING_DIM)))
    created_at: datetime | None = Field(default=None, sa_column=_created_at())
    updated_at: datetime | None = Field(default=None, sa_column=_updated_at())

    aliases: list["SkillAlias"] = Relationship(back_populates="skill", cascade_delete=True)


class SkillAlias(SQLModel, table=True):
    """본문 매칭용 표기 변형. alias 는 소문자·공백제거 정규화 후 저장한다."""

    __tablename__ = "skill_alias"
    __table_args__ = (
        Index("skill_alias_skill_idx", "skill_id"),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key=f"{SCHEMA}.skill.id", ondelete="CASCADE")
    alias: str = Field(max_length=120, unique=True)

    skill: Skill | None = Relationship(back_populates="aliases")


class SkillField(SQLModel, table=True):
    """스킬 ↔ 분야 다대다 (Kotlin 은 backend + android)."""

    __tablename__ = "skill_field"
    __table_args__ = (
        Index("skill_field_field_idx", "field_id"),
        {"schema": SCHEMA},
    )

    skill_id: int = Field(primary_key=True, foreign_key=f"{SCHEMA}.skill.id", ondelete="CASCADE")
    field_id: int = Field(
        primary_key=True, foreign_key=f"{SCHEMA}.tech_field.id", ondelete="CASCADE"
    )


class PostingSkill(SQLModel, table=True):
    """공고 ↔ 스킬. 트렌드 · 동시출현 · 갭분석의 기반 테이블."""

    __tablename__ = "posting_skill"
    __table_args__ = (
        CheckConstraint(
            enums.sql_in("requirement", enums.Requirement),
            name="posting_skill_requirement_chk",
        ),
        # 스킬 기준 역방향 조회 (get_popular_skills · get_skill_demand)
        Index("posting_skill_skill_idx", "skill_id", "requirement"),
        {"schema": SCHEMA},
    )

    posting_id: int = Field(
        primary_key=True,
        sa_type=BigInteger,
        foreign_key=f"{SCHEMA}.job_posting.id",
        ondelete="CASCADE",
    )
    skill_id: int = Field(primary_key=True, foreign_key=f"{SCHEMA}.skill.id", ondelete="CASCADE")
    requirement: enums.Requirement = Field(sa_type=String(16))
    mentions: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    posting: JobPosting | None = Relationship(back_populates="skills")


# ═══════════════════════════════════════════════════════════════════════════
#  임베딩 청크
# ═══════════════════════════════════════════════════════════════════════════
class PostingChunk(SQLModel, table=True):
    __tablename__ = "posting_chunk"
    __table_args__ = (
        CheckConstraint(
            enums.sql_in("section", enums.ChunkSection),
            name="posting_chunk_section_chk",
        ),
        UniqueConstraint("posting_id", "section", "seq", name="posting_chunk_uk"),
        Index("posting_chunk_section_idx", "section"),
        Index(
            "posting_chunk_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with=_HNSW,
        ),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    posting_id: int = Field(
        sa_type=BigInteger,
        foreign_key=f"{SCHEMA}.job_posting.id",
        ondelete="CASCADE",
    )
    section: enums.ChunkSection = Field(sa_type=String(20))
    seq: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    content: str = Field(sa_type=Text)
    chunk_hash: str = Field(sa_column=Column(CHAR(64), nullable=False))  # 변경분만 재임베딩
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector(EMBEDDING_DIM)))
    token_count: int | None = None
    created_at: datetime | None = Field(default=None, sa_column=_created_at())

    posting: JobPosting | None = Relationship(back_populates="chunks")


# ═══════════════════════════════════════════════════════════════════════════
#  실행 이력
# ═══════════════════════════════════════════════════════════════════════════
class CrawlRun(SQLModel, table=True):
    __tablename__ = "crawl_run"
    __table_args__ = (
        CheckConstraint(enums.sql_in("kind", enums.RunKind), name="crawl_run_kind_chk"),
        CheckConstraint(enums.sql_in("status", enums.RunStatus), name="crawl_run_status_chk"),
        Index("crawl_run_kind_started_idx", "kind", text("started_at DESC")),
        # get_data_coverage 의 "최종 수집시각" 조회용
        Index("crawl_run_source_started_idx", "source", text("started_at DESC")),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    kind: enums.RunKind = Field(sa_type=String(16))
    source: str | None = Field(default=None, max_length=20)
    keyword: str | None = Field(default=None, max_length=120)
    status: enums.RunStatus = Field(
        default=enums.RunStatus.RUNNING,
        sa_type=String(16),
        sa_column_kwargs={"server_default": text("'running'")},
    )

    fetched: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    inserted: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    updated: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    skipped: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    embedded: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    errors: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})

    message: str | None = Field(default=None, sa_type=Text)  # 실패 사유 · 마지막 예외
    started_at: datetime | None = Field(default=None, sa_column=_created_at())
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
