from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Payload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class PopularTechStack(_Payload):
    rank: int
    name: str
    search_count: int = Field(alias="searchCount")
    previous_search_count: int = Field(alias="previousSearchCount")
    change_count: int = Field(alias="changeCount")
    change_rate: Optional[float] = Field(default=None, alias="changeRate")
    trend: str
    tech_stack_id: Optional[int] = Field(default=None, alias="techStackId")


class TechChange(_Payload):
    skill_name: str = Field(alias="skillName")
    current_count: int = Field(alias="currentCount")
    previous_count: int = Field(alias="previousCount")
    change_rate: Optional[float] = Field(default=None, alias="changeRate")
    skill_id: Optional[int] = Field(default=None, alias="skillId")


class TechMention(_Payload):
    name: str
    current: int
    previous: int


class ReportFacts(BaseModel):
    report_id: int
    week_start_date: date
    major_name: Optional[str]
    posting_count: Optional[int]
    popular: list[PopularTechStack]
    mentions: list[TechMention]
    max_increase: Optional[TechChange]
    max_decrease: Optional[TechChange]


class GenerateRequest(BaseModel):
    week_start_date: date
    force: bool = False


class GenerateAccepted(BaseModel):
    week_start_date: date
    queued: bool


class ReportReady(BaseModel):
    week_start_date: date
    total: int
    generated: int
    skipped: int
    pending: int