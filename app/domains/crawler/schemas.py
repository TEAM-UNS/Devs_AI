# 사이트 어댑터가 넘기는 정규화 전 공고 모델

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RawJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_job_id: str
    url: str
    title: str
    company_name: str

    tech_stacks: list[str] = Field(default_factory=list)
    job_categories: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)

    career_min: Optional[int] = None
    career_max: Optional[int] = None
    newcomer: bool = False
    education: Optional[str] = None
    employment_type: Optional[str] = None

    published_at: Optional[datetime] = None

    @field_validator("published_at")
    @classmethod
    def _no_future_posting_date(cls, value: Optional[datetime]) -> Optional[datetime]:
        """★ 등록일이 미래면 버린다.

        사이트가 실제로 미래 값을 준다 — 사람인은 접수 예정 공고의 시작일을,
        점핏은 publishedAt 을 게시 예정일로 준다(실측 6건, 최대 20일 뒤).
        미래 날짜는 어떤 기간 조건에도 항상 걸려서 "오늘 올라온 공고" 에도
        "이번 주 공고" 에도 들어간다. 모르는 것으로 두는 편이 낫다.
        """
        if value is None:
            return None
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return None if moment > datetime.now(UTC) else value
    closed_at: Optional[datetime] = None

    detail_fetched: bool = False
    responsibility: Optional[str] = None
    qualifications: Optional[str] = None
    preferred_requirements: Optional[str] = None
    welfares: Optional[str] = None
    recruit_process: Optional[str] = None

    salary_raw: Optional[str] = None

    body_is_image: bool = False
    image_urls: list[str] = Field(default_factory=list)
    body_extract_failed: bool = False

    company_service_info: Optional[str] = None
    company_url: Optional[str] = None
    company_establish_date: Optional[str] = None
    company_source_id: Optional[str] = None
    company_tags: list[str] = Field(default_factory=list)
    company_industry: Optional[str] = None
    company_employee_count: Optional[int] = None
    company_revenue: Optional[int] = None

    raw: dict = Field(default_factory=dict)

    def merged(self, update: dict[str, Any]) -> "RawJob":
        return type(self).model_validate({**self.model_dump(), **update})

    def build_description(self) -> Optional[str]:
        parts: list[str] = []
        for header, body in (
            ("[주요업무]", self.responsibility),
            ("[자격요건]", self.qualifications),
            ("[우대사항]", self.preferred_requirements),
        ):
            if body and body.strip():
                parts.append(f"{header}\n{body.strip()}")
        return "\n\n".join(parts) if parts else None

    def build_welfare(self) -> Optional[str]:
        parts = [p.strip() for p in (self.welfares, self.recruit_process) if p and p.strip()]
        return "\n\n".join(parts) if parts else None

    def content_hash(self) -> str:
        payload = "␟".join(
            [
                self.title,
                self.company_name,
                ",".join(sorted(self.tech_stacks)),
                ",".join(sorted(self.job_categories)),
                ",".join(sorted(self.locations)),
                str(self.career_min),
                str(self.career_max),
                self.build_description() or "",
                self.build_welfare() or "",
                self.closed_at.isoformat() if self.closed_at else "",
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SkillDictionaryRow:
    skill_id: int
    name: str
    is_ambiguous: bool = False
    is_common: bool = False
    aliases: list[str] = field(default_factory=list)
    cs_aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UnmatchedTag:
    tag: str
    count: int
