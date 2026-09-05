"""RawJob — 사이트 어댑터가 뱉는 정규화 이전 형태.

사이트별 필드 차이는 전부 여기서 흡수한다.
market 의 컬럼으로 옮기는 변환은 service.py 책임.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SECTION_RESPONSIBILITY = "[주요업무]"
SECTION_QUALIFICATION = "[자격요건]"
SECTION_PREFERRED = "[우대사항]"


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

    career_min: int | None = None
    career_max: int | None = None
    newcomer: bool = False
    education: str | None = None
    employment_type: str | None = None

    published_at: datetime | None = None
    closed_at: datetime | None = None

    # ── 상세에서만 채워지는 것 ────────────────────────────────────────────
    detail_fetched: bool = False
    responsibility: str | None = None
    qualifications: str | None = None
    preferred_requirements: str | None = None
    welfares: str | None = None
    recruit_process: str | None = None

    salary_raw: str | None = None

    body_is_image: bool = False
    image_urls: list[str] = Field(default_factory=list)
    body_extract_failed: bool = False

    # ── 기업 정보 ────────────────────────────────────────────────────────
    company_service_info: str | None = None
    company_url: str | None = None
    company_establish_date: str | None = None
    company_source_id: str | None = None
    company_tags: list[str] = Field(default_factory=list)
    company_industry: str | None = None
    company_employee_count: int | None = None
    company_revenue: int | None = None

    raw: dict = Field(default_factory=dict)

    # ── 파생 ──────────────────────────────────────────────────────────────
    def merged(self, update: dict[str, Any]) -> RawJob:
        return type(self).model_validate({**self.model_dump(), **update})

    def build_description(self) -> str | None:
        parts: list[str] = []
        for header, body in (
            (SECTION_RESPONSIBILITY, self.responsibility),
            (SECTION_QUALIFICATION, self.qualifications),
            (SECTION_PREFERRED, self.preferred_requirements),
        ):
            if body and body.strip():
                parts.append(f"{header}\n{body.strip()}")
        return "\n\n".join(parts) if parts else None

    def build_welfare(self) -> str | None:
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
