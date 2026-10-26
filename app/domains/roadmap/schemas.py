from dataclasses import dataclass


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