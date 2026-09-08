"""★ 읽기 전용 — 챗봇 툴의 유일한 DB 진입점 (R3).

ORM 엔티티가 아니라 schemas.py 의 DTO 를 반환한다.
집계는 여기서 SQL 로 끝낸다. 툴 함수는 조립만 한다.

트렌드
    popular_skills(field, size_type, career_level, days, top)
    rising_skills(field, min_count, top)      2주 구간 비교 + (this+1)/(last+1)-1
    stacks_by_segment(group_by, field, top)   size · career · location
    salary_stats(...)                         중앙값/사분위 + disclosure_rate
                                              (salary_type != negotiable 만 집계,
                                               분모는 전체 공고수)
기술 관계
    related_skills(skill, field, requirement, top)   동시출현 + NPMI
    skill_demand(skill)                              분야·규모·경력 분포
    resolve_skill(query)                             skill.embedding 코사인

기업
    company_profile(name)          동명 다수면 후보 목록 반환
    similar_companies(company_id)  similarity.py 에 위임
    compare_companies(ids)
    search_companies(query_vec, filters)   pgvector + 메타 필터 한 쿼리

탐색·메타
    search_postings(query_vec, filters, section)  posting_chunk 벡터 검색
    skill_gap(my_skills, field, company_ids)      requirement in (required, tag)
    data_coverage()                               수집 기간 · 총 공고수 · 최종 수집시각

주의: 표본이 작은 결과에는 sample_size 를 반드시 함께 실어 보낸다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.market.enums import CareerLevel, CompanySize, Requirement, RunKind, SalaryType

from app.domains.market.enums import TechField as TechFieldCode
from app.domains.market.models import (
    Company,
    CrawlRun,
    JobPosting,
    PostingSkill,
    Skill,
    TechField,
)
from app.domains.market.schemas import DataCoverage, PopularSkills, SkillCount


# 공용 표현식 
APPEARED_AT = func.coalesce(JobPosting.posted_at, JobPosting.created_at)

_DISCLOSED = (SalaryType.RANGE, SalaryType.MIN_ONLY, SalaryType.MAX_ONLY)

_DEMAND = (Requirement.TAG, Requirement.REQUIRED, Requirement.PREFERRED)

_CAREER_RANGES: dict[CareerLevel, tuple[int, Optional[int]]] = {
    CareerLevel.NEWCOMER: (0, 0),
    CareerLevel.JUNIOR: (1, 3),
    CareerLevel.MID: (4, 7),
    CareerLevel.SENIOR: (8, None),
}


class ChatQueries:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def data_coverage(self) -> DataCoverage:
        totals = (
            await self.session.exec(
                select(
                    func.min(JobPosting.created_at),
                    func.max(JobPosting.created_at),
                    func.count(JobPosting.id),
                    func.count(JobPosting.id).filter(
                        JobPosting.expires_at.is_(None) | (JobPosting.expires_at >= func.now())
                    ),
                    func.count(JobPosting.id).filter(JobPosting.body_is_image.is_(True)),
                    func.count(JobPosting.id).filter(JobPosting.salary_type.in_(_DISCLOSED)),
                    func.count(JobPosting.id).filter(JobPosting.field_id.is_(None)),
                )
            )
        ).one()
        first, last, total, active, image_only, disclosed, unclassified = totals

        by_field = dict(
            (
                await self.session.exec(
                    select(TechField.code, func.count())
                    .join(JobPosting, JobPosting.field_id == TechField.id)
                    .group_by(TechField.code)
                    .order_by(func.count().desc())
                )
            ).all()
        )

        requirement_breakdown = dict(
            (
                await self.session.exec(
                    select(PostingSkill.requirement, func.count()).group_by(
                        PostingSkill.requirement
                    )
                )
            ).all()
        )

        company_count = (await self.session.exec(select(func.count()).select_from(Company))).one()

        last_crawl_at = (
            await self.session.exec(
                select(func.max(CrawlRun.finished_at)).where(
                    CrawlRun.kind == RunKind.CRAWL
                )
            )
        ).one()

        return DataCoverage(
            collected_from=first.date(),
            collected_to=last.date(),
            total_postings=total,
            active_postings=active,
            image_only_ratio=round(image_only / total, 4) if total else 0.0,
            salary_disclosure_rate=round(disclosed / total, 4) if total else 0.0,
            requirement_breakdown=requirement_breakdown,
            by_field=by_field,
            unclassified_postings=unclassified,
            company_count=company_count,
            last_crawl_at=last_crawl_at,
        )

    @staticmethod
    def _posting_filters(
        field: Optional[TechFieldCode],
        size_type: Optional[CompanySize],
        career_level: Optional[CareerLevel],
        days: Optional[int],
    ) -> list[Any]:
        conditions: list[Any] = []

        if days is not None:
            conditions.append(APPEARED_AT >= datetime.now(UTC) - timedelta(days=days))

        if field is not None:
            conditions.append(
                JobPosting.field_id
                == select(TechField.id).where(TechField.code == field).scalar_subquery()
            )

        if size_type is not None:
            conditions.append(
                JobPosting.company_id.in_(select(Company.id).where(Company.size_type == size_type))
            )

        if career_level is not None:
            low, high = _CAREER_RANGES[career_level]
            if high is not None:
                conditions.append(func.coalesce(JobPosting.career_min, 0) <= high)
            conditions.append(JobPosting.career_max.is_(None) | (JobPosting.career_max >= low))

        return conditions

    async def popular_skills(
        self,
        field: Optional[TechFieldCode] = None,
        size_type: Optional[CompanySize] = None,
        career_level: Optional[CareerLevel] = None,
        days: Optional[int] = 30,
        top: int = 20,
        include_common: bool = False,
    ) -> PopularSkills:
        filters = self._posting_filters(
            field=field, size_type=size_type, career_level=career_level, days=days
        )

        skill_filters = [PostingSkill.requirement.in_(_DEMAND)]
        if not include_common:
            skill_filters.append(Skill.is_common.is_(False))

        posting_count = func.count(func.distinct(PostingSkill.posting_id))

        rows = (
            await self.session.exec(
                select(Skill.name, posting_count)
                .join(PostingSkill, PostingSkill.skill_id == Skill.id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*filters, *skill_filters)
                .group_by(Skill.name)
                .order_by(posting_count.desc(), Skill.name)
                .limit(top)
            )
        ).all()

        analyzed = (
            await self.session.exec(
                select(func.count(func.distinct(PostingSkill.posting_id)))
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*filters, *skill_filters)
            )
        ).one()

        total = (
            await self.session.exec(select(func.count()).select_from(JobPosting).where(*filters))
        ).one()

        return PopularSkills(
            items=[
                SkillCount(
                    rank=index,
                    skill=name,
                    posting_count=count,
                    share=round(count / analyzed, 4) if analyzed else 0.0,
                )
                for index, (name, count) in enumerate(rows, start=1)
            ],
            analyzed_postings=analyzed,
            total_postings=total,
            days=days or 0,
        )