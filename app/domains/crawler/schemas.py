"""RawJob — 사이트 어댑터가 뱉는 정규화 이전 형태.

사이트별 필드 차이는 전부 여기서 흡수한다.
market 의 컬럼으로 옮기는 변환은 service.py 책임.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field

# 섹션 헤더. chunker 가 나중에 이 헤더로 다시 잘라낸다.
SECTION_RESPONSIBILITY = "[주요업무]"
SECTION_QUALIFICATION = "[자격요건]"
SECTION_PREFERRED = "[우대사항]"


class RawJob(BaseModel):
    """수집 직후의 공고 1건."""

    source: str
    source_job_id: str
    url: str
    title: str
    company_name: str

    # 사이트가 제공한 스택 태그 (점핏·원티드). 없으면 빈 리스트.
    tech_stacks: list[str] = Field(default_factory=list)
    # 사이트의 직무 분류 문자열. tech_field 매핑 입력.
    job_categories: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)

    career_min: int | None = None
    career_max: int | None = None
    newcomer: bool = False
    education: str | None = None

    published_at: datetime | None = None
    closed_at: datetime | None = None

    # ── 상세에서만 채워지는 것 ────────────────────────────────────────────
    detail_fetched: bool = False
    responsibility: str | None = None
    qualifications: str | None = None
    preferred_requirements: str | None = None
    welfares: str | None = None
    recruit_process: str | None = None

    # ── 기업 정보 ────────────────────────────────────────────────────────
    company_service_info: str | None = None  # 서비스/회사 소개
    company_url: str | None = None
    company_establish_date: str | None = None
    company_source_id: str | None = None  # 사이트 내부 기업 식별자
    company_tags: list[str] = Field(default_factory=list)  # "대기업" 등

    # 파서가 못 옮긴 원본. 나중에 컬럼을 늘릴 때 재수집 없이 채울 수 있다.
    raw: dict = Field(default_factory=dict)

    # ── 파생 ──────────────────────────────────────────────────────────────
    def build_description(self) -> str | None:
        """본문 3섹션을 하나로 합친다. 섹션 헤더는 chunker 가 다시 쓴다."""
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
        """변경 감지용. 값이 그대로면 재추출을 건너뛴다.

        조회수·스크랩수처럼 매번 바뀌는 필드는 절대 넣지 않는다.
        넣으면 모든 공고가 매일 '변경됨' 으로 잡힌다.
        """
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
