from typing import Optional

from dataclasses import dataclass
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class SkillDemand:
    name: str
    posting_count: int
    share: float


@dataclass(frozen=True)
class RoadmapFacts:
    field_name: str
    window_days: int
    total_postings: int
    user_skills: list[str]

    demand: list[SkillDemand]

    companions: dict[str, list[str]]

    gaps: list[SkillDemand]

    def has_evidence(self) -> bool:
        return self.total_postings > 0 and bool(self.demand)


class OutlineWeek(BaseModel):
    week: int = Field(description="주차 번호")
    theme: str = Field(description="그 주에 다룰 주제 한 줄")


class RoadmapOutline(BaseModel):
    weeks: list[OutlineWeek]
    summary: str = Field(description="로드맵 전체 요약 2~3문장")


class DayItem(BaseModel):
    title: str = Field(description="그날 할 일 한 줄")
    description: str = Field(description="왜 필요한지 또는 무엇을 확인하면 되는지 1~2문장")


class RoadmapDay(BaseModel):
    day: int
    items: list[DayItem]


class RoadmapWeek(BaseModel):
    week: int
    days: list[RoadmapDay]


class RoadmapGenerateRequest(BaseModel):
    roadmap_id: int
    additional_comment: Optional[str] = Field(default=None, max_length=500)