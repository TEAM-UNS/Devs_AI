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
    - CHECK 제약 문자열은 enums.py + core.database.sql_in 으로 생성한다
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

from app.core.config import get_settings
from app.domains.market import enums
from app.domains.market.enums import sql_in

SCHEMA = "market"

EMBED_DIM = get_settings().embed_dim

_HNSW = {"m": 16, "ef_construction": 64}

# ★ Enum 컬럼은 반드시 sa_type=String(n) 으로 고정한다.


def _created_at() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
class TechField(SQLModel, table=True):
    __tablename__ = "tech_field"
    __table_args__ = {"schema": SCHEMA}

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(max_length=32, unique=True)
    name: str = Field(max_length=64)
    sort_order: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    created_at: datetime | None = Field(default=None, sa_column=_created_at())

    postings: list["JobPosting"] = Relationship(back_populates="field")


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
class Company(SQLModel, table=True):
    __tablename__ = "company"
    __table_args__ = (
        CheckConstraint(sql_in("size_type", enums.CompanySize), name="company_size_type_chk"),
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

    name_key: str = Field(max_length=200, unique=True)
    name: str = Field(max_length=200)

    description: str | None = Field(default=None, sa_type=Text)
    business_content: str | None = Field(default=None, sa_type=Text)
    talent_profile: str | None = Field(default=None, sa_type=Text)

    size_type: enums.CompanySize = Field(
        default=enums.CompanySize.UNKNOWN,
        sa_type=String(20),
        sa_column_kwargs={"server_default": text("'unknown'")},
    )
    employee_count: int | None = None
    industry: str | None = Field(default=None, max_length=120)
    founded: str | None = Field(default=None, max_length=20)
    revenue: int | None = Field(default=None, sa_type=BigInteger)
    homepage: str | None = Field(default=None, max_length=500)

    profile_embedding: list[float] | None = Field(
        default=None, sa_column=Column(Vector(EMBED_DIM))
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
    __tablename__ = "company_source"
    __table_args__ = (
        CheckConstraint(
            sql_in("source", enums.CrawlSource), name="company_source_source_chk"
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
# ═══════════════════════════════════════════════════════════════════════════
class JobPosting(SQLModel, table=True):
    __tablename__ = "job_posting"
    __table_args__ = (
        CheckConstraint(sql_in("source", enums.CrawlSource), name="job_posting_source_chk"),
        CheckConstraint(
            sql_in("salary_type", enums.SalaryType),
            name="job_posting_salary_type_chk",
        ),
        CheckConstraint(
            f"salary_period IS NULL OR {sql_in('salary_period', enums.SalaryPeriod)}",
            name="job_posting_salary_period_chk",
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
        Index(
            "job_posting_salary_idx",
            "field_id",
            "salary_min",
            "salary_max",
            postgresql_where=text("salary_type IN ('range', 'min_only', 'max_only')"),
        ),
        Index(
            "job_posting_embed_todo_idx",
            "id",
            postgresql_where=text(
                "body_is_image = false AND body_extract_failed = false "
                "AND description IS NOT NULL "
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

    # ── 정규화 전 원본 ──────────────────────────────────────────────────
    company_name_raw: str | None = Field(default=None, max_length=200)
    tags_raw: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )

    career_min: int | None = None
    career_max: int | None = None
    employment_type: str | None = Field(default=None, max_length=30)
    education: str | None = Field(default=None, max_length=30)
    location: str | None = Field(default=None, max_length=120)

    description: str | None = Field(default=None, sa_type=Text)
    welfare: str | None = Field(default=None, sa_type=Text)

    salary_raw: str | None = Field(default=None, sa_type=Text)
    # ★ 항상 "연봉 만원" 단위다. 월급 표기는 ×12 해서 저장한다.
    salary_min: int | None = None
    salary_max: int | None = None
    salary_type: enums.SalaryType = Field(
        default=enums.SalaryType.UNKNOWN,
        sa_type=String(16),
        sa_column_kwargs={"server_default": text("'unknown'")},
    )
    salary_period: enums.SalaryPeriod | None = Field(default=None, sa_type=String(8))

    body_is_image: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    body_extract_failed: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("false")}
    )
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
# ═══════════════════════════════════════════════════════════════════════════
class Skill(SQLModel, table=True):
    __tablename__ = "skill"
    __table_args__ = {"schema": SCHEMA}

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=80, unique=True)
    category: str | None = Field(default=None, max_length=32)
    is_ambiguous: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    is_common: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector(EMBED_DIM)))
    created_at: datetime | None = Field(default=None, sa_column=_created_at())
    updated_at: datetime | None = Field(default=None, sa_column=_updated_at())

    aliases: list["SkillAlias"] = Relationship(back_populates="skill", cascade_delete=True)


class SkillAlias(SQLModel, table=True):
    __tablename__ = "skill_alias"
    __table_args__ = (
        Index("skill_alias_skill_idx", "skill_id"),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key=f"{SCHEMA}.skill.id", ondelete="CASCADE")
    alias: str = Field(max_length=120, unique=True)
    case_sensitive: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})

    skill: Skill | None = Relationship(back_populates="aliases")


class SkillField(SQLModel, table=True):
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
    __tablename__ = "posting_skill"
    __table_args__ = (
        CheckConstraint(
            sql_in("requirement", enums.Requirement),
            name="posting_skill_requirement_chk",
        ),
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
# ═══════════════════════════════════════════════════════════════════════════
class PostingChunk(SQLModel, table=True):
    __tablename__ = "posting_chunk"
    __table_args__ = (
        CheckConstraint(
            sql_in("section", enums.ChunkSection),
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
    chunk_hash: str = Field(sa_column=Column(CHAR(64), nullable=False))
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector(EMBED_DIM)))
    token_count: int | None = None
    created_at: datetime | None = Field(default=None, sa_column=_created_at())

    posting: JobPosting | None = Relationship(back_populates="chunks")


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
class CrawlRun(SQLModel, table=True):
    __tablename__ = "crawl_run"
    __table_args__ = (
        CheckConstraint(sql_in("kind", enums.RunKind), name="crawl_run_kind_chk"),
        CheckConstraint(sql_in("status", enums.RunStatus), name="crawl_run_status_chk"),
        Index("crawl_run_kind_started_idx", "kind", text("started_at DESC")),
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

    message: str | None = Field(default=None, sa_type=Text)
    started_at: datetime | None = Field(default=None, sa_column=_created_at())
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
