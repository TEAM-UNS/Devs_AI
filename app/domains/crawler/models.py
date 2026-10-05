# 공고, 기업, 스킬 테이블 모델 (DB 스키마 market)

# from __future__ import annotations 를 쓰면 Relationship 해석이 깨진다
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, BigInteger, Column, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

from app.core.config import get_settings
from app.domains.crawler import enums

settings = get_settings()


def _timestamp() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TechField(SQLModel, table=True):
    __tablename__ = "tech_field"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(max_length=32, unique=True)
    name: str = Field(max_length=64)
    sort_order: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    postings: list["JobPosting"] = Relationship(back_populates="field")


class Company(SQLModel, table=True):
    __tablename__ = "company"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    name_key: str = Field(max_length=200, unique=True)
    name: str = Field(max_length=200)
    description: Optional[str] = Field(default=None, sa_type=Text)
    business_content: Optional[str] = Field(default=None, sa_type=Text)
    talent_profile: Optional[str] = Field(default=None, sa_type=Text)
    size_type: enums.CompanySize = Field(
        default=enums.CompanySize.UNKNOWN,
        sa_type=String(20),
        index=True,
        sa_column_kwargs={"server_default": text("'unknown'")},
    )
    employee_count: Optional[int] = None
    industry: Optional[str] = Field(default=None, max_length=120)
    founded: Optional[str] = Field(default=None, max_length=20)
    revenue: Optional[int] = Field(default=None, sa_type=BigInteger)
    homepage: Optional[str] = Field(default=None, max_length=500)
    profile_embedding: Optional[list[float]] = Field(default=None, sa_type=Vector(settings.embed_dim))
    embed_hash: Optional[str] = Field(default=None, sa_type=CHAR(64))
    raw_fields: dict = Field(
        default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": text("'{}'::jsonb")}
    )
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())
    updated_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    sources: list["CompanySource"] = Relationship(back_populates="company", cascade_delete=True)
    postings: list["JobPosting"] = Relationship(back_populates="company")


class CompanySource(SQLModel, table=True):
    __tablename__ = "company_source"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    company_id: int = Field(
        sa_type=BigInteger, foreign_key="market.company.id", ondelete="CASCADE", index=True
    )
    source: enums.CrawlSource = Field(sa_type=String(20))
    source_company_id: str = Field(max_length=100)
    url: Optional[str] = Field(default=None, max_length=500)
    collected_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    company: Optional[Company] = Relationship(back_populates="sources")


class JobPosting(SQLModel, table=True):
    __tablename__ = "job_posting"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    source: enums.CrawlSource = Field(sa_type=String(20))
    source_job_id: str = Field(max_length=100)
    company_id: Optional[int] = Field(
        default=None, sa_type=BigInteger, foreign_key="market.company.id", ondelete="SET NULL", index=True
    )
    field_id: Optional[int] = Field(default=None, foreign_key="market.tech_field.id", ondelete="SET NULL")
    title: str = Field(max_length=300)
    company_name_raw: Optional[str] = Field(default=None, max_length=200)
    tags_raw: list[str] = Field(
        default_factory=list, sa_type=JSONB, sa_column_kwargs={"server_default": text("'[]'::jsonb")}
    )
    career_min: Optional[int] = None
    career_max: Optional[int] = None
    employment_type: Optional[str] = Field(default=None, max_length=30)
    education: Optional[str] = Field(default=None, max_length=30)
    location: Optional[str] = Field(default=None, max_length=120, index=True)
    description: Optional[str] = Field(default=None, sa_type=Text)
    welfare: Optional[str] = Field(default=None, sa_type=Text)
    salary_raw: Optional[str] = Field(default=None, sa_type=Text)
    # 항상 연봉 만원 단위. 월급은 12를 곱해 저장한다
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_type: enums.SalaryType = Field(
        default=enums.SalaryType.UNKNOWN,
        sa_type=String(16),
        sa_column_kwargs={"server_default": text("'unknown'")},
    )
    salary_period: Optional[enums.SalaryPeriod] = Field(default=None, sa_type=String(8))
    body_is_image: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    body_extract_failed: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    posted_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
    expires_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
    content_hash: Optional[str] = Field(default=None, sa_type=CHAR(64))
    embed_hash: Optional[str] = Field(default=None, sa_type=CHAR(64))
    collected_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())
    raw_fields: dict = Field(
        default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": text("'{}'::jsonb")}
    )
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())
    updated_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    company: Optional[Company] = Relationship(back_populates="postings")
    field: Optional[TechField] = Relationship(back_populates="postings")
    skills: list["PostingSkill"] = Relationship(back_populates="posting", cascade_delete=True)
    chunks: list["PostingChunk"] = Relationship(back_populates="posting", cascade_delete=True)


class Skill(SQLModel, table=True):
    __tablename__ = "skill"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=80, unique=True)
    category: Optional[str] = Field(default=None, max_length=32)
    is_ambiguous: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    is_common: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    embedding: Optional[list[float]] = Field(default=None, sa_type=Vector(settings.embed_dim))
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())
    updated_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    aliases: list["SkillAlias"] = Relationship(back_populates="skill", cascade_delete=True)


class SkillAlias(SQLModel, table=True):
    __tablename__ = "skill_alias"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key="market.skill.id", ondelete="CASCADE", index=True)
    alias: str = Field(max_length=120, unique=True)
    case_sensitive: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})

    skill: Optional[Skill] = Relationship(back_populates="aliases")


class SkillField(SQLModel, table=True):
    __tablename__ = "skill_field"
    __table_args__ = {"schema": "market"}

    skill_id: int = Field(primary_key=True, foreign_key="market.skill.id", ondelete="CASCADE")
    field_id: int = Field(
        primary_key=True, foreign_key="market.tech_field.id", ondelete="CASCADE", index=True
    )


class PostingSkill(SQLModel, table=True):
    __tablename__ = "posting_skill"
    __table_args__ = {"schema": "market"}

    posting_id: int = Field(
        primary_key=True, sa_type=BigInteger, foreign_key="market.job_posting.id", ondelete="CASCADE"
    )
    skill_id: int = Field(primary_key=True, foreign_key="market.skill.id", ondelete="CASCADE")
    requirement: enums.Requirement = Field(sa_type=String(16))
    mentions: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    posting: Optional[JobPosting] = Relationship(back_populates="skills")


class PostingChunk(SQLModel, table=True):
    __tablename__ = "posting_chunk"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    posting_id: int = Field(sa_type=BigInteger, foreign_key="market.job_posting.id", ondelete="CASCADE")
    section: enums.ChunkSection = Field(sa_type=String(20), index=True)
    seq: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    content: str = Field(sa_type=Text)
    chunk_hash: str = Field(sa_type=CHAR(64))
    embedding: Optional[list[float]] = Field(default=None, sa_type=Vector(settings.embed_dim))
    token_count: Optional[int] = None
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    posting: Optional[JobPosting] = Relationship(back_populates="chunks")


class CrawlRun(SQLModel, table=True):
    __tablename__ = "crawl_run"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    kind: enums.RunKind = Field(sa_type=String(16))
    source: Optional[str] = Field(default=None, max_length=20)
    keyword: Optional[str] = Field(default=None, max_length=120)
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
    message: Optional[str] = Field(default=None, sa_type=Text)
    started_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())
    finished_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
