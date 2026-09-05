"""market DTO — ORM 엔티티가 도메인 밖으로 새지 않게 하는 경계.

지금은 크롤러가 쓰는 것만 있다. 챗봇 툴용 DTO(SalaryStats · SkillRelation 등)는
queries.py 를 구현할 때 여기에 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


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


@dataclass(frozen=True)
class DataCoverage:
    collected_from: date
    collected_to: date
    total_postings: int
    active_postings: int
    image_only_ratio: float
    salary_disclosure_rate: float
    requirement_breakdown: dict[str, int]
    by_field: dict[str, int]
    # ★ field_id IS NULL 인 공고. by_field 에 넣으면 "미분류" 가 분야인 것처럼
    unclassified_postings: int
    company_count: int
    last_crawl_at: datetime | None
