# 챗봇 쿼리 결과 DTO

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


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
    unclassified_postings: int
    company_count: int
    last_crawl_at: Optional[datetime]


@dataclass(frozen=True)
class SkillCount:
    rank: int
    skill: str
    posting_count: int
    share: float


@dataclass(frozen=True)
class PopularSkills:
    items: list[SkillCount]
    analyzed_postings: int
    total_postings: int
    days: int


@dataclass(frozen=True)
class SkillDemand:
    skill: str
    total_postings: int
    by_field: dict[str, int]
    by_size: dict[str, int]
    by_career: dict[str, int]
    by_requirement: dict[str, int]


@dataclass(frozen=True)
class SkillCandidate:
    skill: str
    similarity: float


@dataclass(frozen=True)
class RisingSkill:
    rank: int
    skill: str
    recent_count: int
    previous_count: int
    growth_rate: float


@dataclass(frozen=True)
class RisingSkills:
    items: list[RisingSkill]
    window_days: int
    recent_postings: int
    previous_postings: int
    low_confidence: bool


@dataclass(frozen=True)
class SegmentStacks:
    segment: str
    posting_count: int
    skills: list[SkillCount]


@dataclass(frozen=True)
class StacksBySegment:
    group_by: str
    segments: list[SegmentStacks]


@dataclass(frozen=True)
class SalaryStats:
    sample_size: int
    total_postings: int
    # 분모는 sample_size 가 아니라 total_postings 다
    disclosure_rate: float
    unit: str
    median: Optional[int]
    q1: Optional[int]
    q3: Optional[int]
    minimum: Optional[int]
    maximum: Optional[int]
    breakdown: dict[str, int]
    low_confidence: bool


@dataclass(frozen=True)
class RelatedSkill:
    rank: int
    skill: str
    cooccurrence: int
    npmi: float


@dataclass(frozen=True)
class RelatedSkills:
    skill: str
    base_postings: int
    items: list[RelatedSkill]


@dataclass(frozen=True)
class CompanyCandidate:
    company_id: int
    name: str
    posting_count: int


@dataclass(frozen=True)
class CompanyProfile:
    company_id: int
    name: str
    size_type: str
    employee_count: Optional[int]
    industry: Optional[str]
    founded: Optional[str]
    homepage: Optional[str]
    description: Optional[str]
    talent_profile: Optional[str]
    posting_count: int
    top_skills: list[SkillCount]
    career_distribution: dict[str, int]
    locations: dict[str, int]


@dataclass(frozen=True)
class CompanyLookup:
    status: str
    profile: Optional[CompanyProfile] = None
    candidates: list[CompanyCandidate] = field(default_factory=list)


@dataclass(frozen=True)
class CompanyComparison:
    company_id: int
    name: str
    size_type: str
    posting_count: int
    top_skills: list[SkillCount]


@dataclass(frozen=True)
class CompanyComparisons:
    companies: list[CompanyComparison]
    shared_skills: list[str]


@dataclass(frozen=True)
class SimilarCompany:
    rank: int
    company_id: int
    name: str
    size_type: str
    score: float
    stack_cosine: Optional[float]
    description_cosine: Optional[float]
    description_percentile: Optional[float]
    size_proximity: Optional[float]
    shared_skills: list[str]


@dataclass(frozen=True)
class SimilarCompanies:
    company_id: int
    name: str
    items: list[SimilarCompany]


@dataclass(frozen=True)
class CompanyHit:
    company_id: int
    name: str
    size_type: str
    industry: Optional[str]
    posting_count: int
    evidence: Optional[str]
    similarity: float


@dataclass(frozen=True)
class PostingHit:
    posting_id: int
    title: str
    company: Optional[str]
    url: Optional[str]
    section: str
    chunk: str
    similarity: float


@dataclass(frozen=True)
class SkillGapItem:
    rank: int
    skill: str
    posting_count: int
    share: float


@dataclass(frozen=True)
class SkillGap:
    matched: list[str]
    unknown_inputs: list[str]
    missing: list[SkillGapItem]
    analyzed_postings: int
    coverage: float
