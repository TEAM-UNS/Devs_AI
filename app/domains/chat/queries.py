import math
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from collections.abc import Sequence
from typing import Any, Optional

from sqlmodel import (
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.crawler.enums import (
    CareerLevel,
    ChunkSection,
    CompanySize,
    Requirement,
    RunKind,
    SalaryType,
)

from app.domains.crawler.enums import TechField as TechFieldCode
from app.domains.crawler.models import (
    Company,
    CrawlRun,
    JobPosting,
    PostingChunk,
    PostingSkill,
    Skill,
    TechField,
    SkillAlias,
)
from app.domains.chat.schemas import (
    CompanyCandidate,
    CompanyComparison,
    CompanyComparisons,
    CompanyHit,
    CompanyLookup,
    CompanyProfile,
    PostingHit,
    RelatedSkill,
    RelatedSkills,
    RisingSkill,
    RisingSkills,
    SalaryStats,
    SegmentStacks,
    SimilarCompanies,
    SimilarCompany,
    SkillGap,
    SkillGapItem,
    StacksBySegment,
    DataCoverage,
    PopularSkills,
    SkillCandidate,
    SkillCount,
    SkillDemand,
)


class ChatQueries:
    APPEARED_AT = func.coalesce(JobPosting.posted_at, JobPosting.created_at)

    _DEMAND = (Requirement.TAG, Requirement.REQUIRED, Requirement.PREFERRED)

    _STRICT = (Requirement.TAG, Requirement.REQUIRED)

    # K 없이 나누면 지난 구간 0건인 스킬이 무한대 증가율이 된다
    _RISING_SMOOTHING = 1

    _LOW_CONFIDENCE_SAMPLE = 10

    _CAREER_RANGES: dict[CareerLevel, tuple[int, Optional[int]]] = {
        CareerLevel.NEWCOMER: (0, 0),
        CareerLevel.JUNIOR: (1, 3),
        CareerLevel.MID: (4, 7),
        CareerLevel.SENIOR: (8, None),
    }

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
                    func.count(JobPosting.id).filter(
                        JobPosting.salary_type.in_(
                            (SalaryType.RANGE, SalaryType.MIN_ONLY, SalaryType.MAX_ONLY)
                        )
                    ),
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
            conditions.append(ChatQueries.APPEARED_AT >= datetime.now(UTC) - timedelta(days=days))

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
            low, high = ChatQueries._CAREER_RANGES[career_level]
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

        skill_filters = [PostingSkill.requirement.in_(self._DEMAND)]
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

    async def resolve_skill_name(self, query: str) -> Optional[tuple[int, str]]:
        needle = query.strip()
        row = (
            await self.session.exec(
                select(Skill.id, Skill.name)
                .outerjoin(SkillAlias, SkillAlias.skill_id == Skill.id)
                .where(
                    or_(
                        func.lower(Skill.name) == needle.lower(),
                        and_(
                            SkillAlias.case_sensitive.is_(False),
                            func.lower(SkillAlias.alias) == needle.lower(),
                        ),
                        and_(SkillAlias.case_sensitive.is_(True),
                             SkillAlias.alias == needle
                        ),
                    )
                )
                .limit(1)
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def resolve_skill(
        self, query_vec: Sequence[float], top: int = 5
    ) -> list[SkillCandidate]:
        distance = Skill.embedding.cosine_distance(list(query_vec))
        rows = (
            await self.session.exec(
                select(Skill.name, distance)
                .where(Skill.embedding.is_not(None))
                .order_by(distance)
                .limit(top)
            )
        ).all()
        return [SkillCandidate(skill=name, similarity=round(1 - d, 4)) for name, d in rows]

    async def skill_demand(self, skill_query: str) -> Optional[SkillDemand]:
        found = await self.resolve_skill_name(skill_query)
        if found is None:
            return None
        skill_id, skill_name = found

        base = [PostingSkill.skill_id == skill_id, PostingSkill.requirement.in_(self._DEMAND)]
        postings = func.count(func.distinct(PostingSkill.posting_id))

        def scoped():
            return (
                select(postings)
                .select_from(PostingSkill)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
            )

        total = (await self.session.exec(scoped().where(*base))).one()

        by_field = dict(
            (
                await self.session.exec(
                    select(TechField.code, postings)
                    .select_from(PostingSkill)
                    .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                    .join(TechField, TechField.id == JobPosting.field_id)
                    .where(*base)
                    .group_by(TechField.code)
                    .order_by(postings.desc())
                )
            ).all()
        )

        by_size = dict(
            (
                await self.session.exec(
                    select(Company.size_type, postings)
                    .select_from(PostingSkill)
                    .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                    .join(Company, Company.id == JobPosting.company_id)
                    .where(*base)
                    .group_by(Company.size_type)
                    .order_by(postings.desc())
                )
            ).all()
        )

        career = self._career_conditions()
        row = (
            await self.session.exec(
                select(*[postings.filter(cond).label(name) for name, cond in career.items()])
                .select_from(PostingSkill)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*base)
            )
        ).one()
        by_career = dict(zip(career.keys(), row, strict=True))

        by_requirement = dict(
            (
                await self.session.exec(
                    select(PostingSkill.requirement, func.count())
                    .where(*base)
                    .group_by(PostingSkill.requirement)
                    .order_by(func.count().desc())
                )
            ).all()
        )

        return SkillDemand(
            skill=skill_name,
            total_postings=total,
            by_field=by_field,
            by_size=by_size,
            by_career=by_career,
            by_requirement=by_requirement,
        )

    @staticmethod
    def _career_conditions() -> dict[str, Any]:
        conditions: dict[str, Any] = {}
        for level in CareerLevel:
            low, high = ChatQueries._CAREER_RANGES[level]
            parts = [or_(JobPosting.career_max.is_(None), JobPosting.career_max >= low)]
            if high is not None:
                parts.append(func.coalesce(JobPosting.career_min, 0) <= high)
            conditions[level.value] = and_(*parts)
        return conditions

    async def rising_skills(
        self,
        field: Optional[TechFieldCode] = None,
        window_days: int = 7,
        min_count: int = 5,
        top: int = 10,
        include_common: bool = False,
    ) -> RisingSkills:
        now = datetime.now(UTC)
        recent_from = now - timedelta(days=window_days)
        previous_from = now - timedelta(days=window_days * 2)

        base = self._posting_filters(field=field, size_type=None, career_level=None, days=None)
        base.append(PostingSkill.requirement.in_(self._DEMAND))
        if not include_common:
            base.append(Skill.is_common.is_(False))

        recent = func.count(func.distinct(PostingSkill.posting_id)).filter(
            self.APPEARED_AT >= recent_from
        )
        previous = func.count(func.distinct(PostingSkill.posting_id)).filter(
            and_(self.APPEARED_AT >= previous_from, self.APPEARED_AT < recent_from)
        )

        rows = (
            await self.session.exec(
                select(Skill.name, recent, previous)
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*base, self.APPEARED_AT >= previous_from)
                .group_by(Skill.name)
                .having(recent >= min_count)
            )
        ).all()

        scored = sorted(
            (
                (
                    name,
                    recent_count,
                    previous_count,
                    (recent_count + self._RISING_SMOOTHING)
                    / (previous_count + self._RISING_SMOOTHING)
                    - 1,
                )
                for name, recent_count, previous_count in rows
            ),
            key=lambda row: (-row[3], row[0]),
        )[:top]

        window_counts = (
            await self.session.exec(
                select(
                    func.count().filter(self.APPEARED_AT >= recent_from),
                    func.count().filter(
                        and_(self.APPEARED_AT >= previous_from, self.APPEARED_AT < recent_from)
                    ),
                )
                .select_from(JobPosting)
                .where(
                    *self._posting_filters(
                        field=field, size_type=None, career_level=None, days=None
                    )
                )
            )
        ).one()
        recent_postings, previous_postings = window_counts

        return RisingSkills(
            items=[
                RisingSkill(
                    rank=index,
                    skill=name,
                    recent_count=recent_count,
                    previous_count=previous_count,
                    growth_rate=round(rate, 4),
                )
                for index, (name, recent_count, previous_count, rate) in enumerate(scored, start=1)
            ],
            window_days=window_days,
            recent_postings=recent_postings,
            previous_postings=previous_postings,
            low_confidence=(
                min(recent_postings, previous_postings) < self._LOW_CONFIDENCE_SAMPLE * 10
            ),
        )

    async def stacks_by_segment(
        self,
        group_by: str = "size",
        field: Optional[TechFieldCode] = None,
        top: int = 10,
        days: Optional[int] = 30,
        include_common: bool = False,
    ) -> StacksBySegment:
        if group_by not in ("size", "career", "location"):
            raise ValueError(f"group_by 는 size · career · location 중 하나여야 합니다: {group_by}")

        base = self._posting_filters(
            field=field, size_type=None, career_level=None, days=days
        )
        base.append(PostingSkill.requirement.in_(self._DEMAND))
        if not include_common:
            base.append(Skill.is_common.is_(False))

        if group_by == "career":
            segments = []
            for level, condition in self._career_conditions().items():
                segments.append(await self._segment_stacks(level, base + [condition], top))
            return StacksBySegment(group_by=group_by, segments=segments)

        if group_by == "size":
            label = Company.size_type
            extra_join = (Company, Company.id == JobPosting.company_id)
        else:
            label = func.split_part(JobPosting.location, " ", 1)
            extra_join = None

        posting_count = func.count(func.distinct(PostingSkill.posting_id))
        stmt = (
            select(label, Skill.name, posting_count)
            .select_from(PostingSkill)
            .join(Skill, Skill.id == PostingSkill.skill_id)
            .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
        )
        if extra_join is not None:
            stmt = stmt.join(*extra_join)
        else:
            stmt = stmt.where(JobPosting.location.is_not(None))

        rows = (
            await self.session.exec(
                stmt.where(*base).group_by(label, Skill.name).order_by(posting_count.desc())
            )
        ).all()

        grouped: dict[str, list[tuple[str, int]]] = {}
        for segment, skill_name, count in rows:
            grouped.setdefault(str(segment), []).append((skill_name, count))

        segments = [
            SegmentStacks(
                segment=segment,
                posting_count=max(count for _, count in items),
                skills=[
                    SkillCount(rank=index, skill=name, posting_count=count, share=0.0)
                    for index, (name, count) in enumerate(items[:top], start=1)
                ],
            )
            for segment, items in sorted(
                grouped.items(), key=lambda kv: -max(c for _, c in kv[1])
            )
        ]
        return StacksBySegment(group_by=group_by, segments=segments)

    async def _segment_stacks(
        self, segment: str, conditions: list[Any], top: int
    ) -> SegmentStacks:
        posting_count = func.count(func.distinct(PostingSkill.posting_id))
        rows = (
            await self.session.exec(
                select(Skill.name, posting_count)
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*conditions)
                .group_by(Skill.name)
                .order_by(posting_count.desc(), Skill.name)
                .limit(top)
            )
        ).all()
        return SegmentStacks(
            segment=segment,
            posting_count=rows[0][1] if rows else 0,
            skills=[
                SkillCount(rank=index, skill=name, posting_count=count, share=0.0)
                for index, (name, count) in enumerate(rows, start=1)
            ],
        )

    async def salary_stats(
        self,
        field: Optional[TechFieldCode] = None,
        size_type: Optional[CompanySize] = None,
        career_level: Optional[CareerLevel] = None,
        company_id: Optional[int] = None,
        skill: Optional[str] = None,
        days: Optional[int] = None,
    ) -> SalaryStats:
        filters = self._posting_filters(
            field=field, size_type=size_type, career_level=career_level, days=days
        )
        if company_id is not None:
            filters.append(JobPosting.company_id == company_id)
        if skill is not None:
            found = await self.resolve_skill_name(skill)
            if found is None:
                filters.append(text("false"))
            else:
                filters.append(
                    JobPosting.id.in_(
                        select(PostingSkill.posting_id).where(
                            PostingSkill.skill_id == found[0],
                            PostingSkill.requirement.in_(self._DEMAND),
                        )
                    )
                )

        # max_only 는 대푯값을 정할 수 없어 집계에서 뺀다
        stat_types = (SalaryType.RANGE, SalaryType.MIN_ONLY)
        amount = func.coalesce(JobPosting.salary_min, JobPosting.salary_max)
        in_scope = and_(JobPosting.salary_type.in_(stat_types), amount.is_not(None))

        row = (
            await self.session.exec(
                select(
                    func.count(),
                    func.count().filter(in_scope),
                    func.percentile_cont(0.5).within_group(amount).filter(in_scope),
                    func.percentile_cont(0.25).within_group(amount).filter(in_scope),
                    func.percentile_cont(0.75).within_group(amount).filter(in_scope),
                    func.min(amount).filter(in_scope),
                    func.max(amount).filter(in_scope),
                )
                .select_from(JobPosting)
                .where(*filters)
            )
        ).one()
        total, sample, median, q1, q3, minimum, maximum = row

        breakdown = dict(
            (
                await self.session.exec(
                    select(JobPosting.salary_type, func.count())
                    .where(*filters)
                    .group_by(JobPosting.salary_type)
                )
            ).all()
        )

        def to_int(value: Any) -> Optional[int]:
            return int(value) if value is not None else None

        return SalaryStats(
            sample_size=sample,
            total_postings=total,
            disclosure_rate=round(sample / total, 4) if total else 0.0,
            unit="만원",
            median=to_int(median),
            q1=to_int(q1),
            q3=to_int(q3),
            minimum=to_int(minimum),
            maximum=to_int(maximum),
            breakdown={str(k): v for k, v in breakdown.items()},
            low_confidence=sample < self._LOW_CONFIDENCE_SAMPLE,
        )

    async def related_skills(
        self,
        skill_query: str,
        field: Optional[TechFieldCode] = None,
        requirement: Optional[Requirement] = None,
        top: int = 10,
        days: Optional[int] = None,
    ) -> Optional[RelatedSkills]:
        found = await self.resolve_skill_name(skill_query)
        if found is None:
            return None
        skill_id, skill_name = found

        grades = (requirement,) if requirement is not None else self._DEMAND
        filters = self._posting_filters(
            field=field, size_type=None, career_level=None, days=days
        )

        scope = select(PostingSkill.posting_id).select_from(PostingSkill)
        if filters:
            scope = scope.join(JobPosting, JobPosting.id == PostingSkill.posting_id)
        scope = scope.where(*filters, PostingSkill.requirement.in_(grades)).subquery()

        universe = (await self.session.exec(select(func.count()).select_from(scope))).one()
        if not universe:
            return RelatedSkills(skill=skill_name, base_postings=0, items=[])

        base_ids = (
            select(PostingSkill.posting_id)
            .where(PostingSkill.skill_id == skill_id, PostingSkill.requirement.in_(grades))
            .subquery()
        )
        base_count = (await self.session.exec(select(func.count()).select_from(base_ids))).one()
        if not base_count:
            return RelatedSkills(skill=skill_name, base_postings=0, items=[])

        cooccurrence = func.count(func.distinct(PostingSkill.posting_id))
        rows = (
            await self.session.exec(
                select(Skill.id, Skill.name, cooccurrence)
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .where(
                    PostingSkill.posting_id.in_(select(base_ids.c.posting_id)),
                    PostingSkill.skill_id != skill_id,
                    PostingSkill.requirement.in_(grades),
                )
                .group_by(Skill.id, Skill.name)
                .having(cooccurrence >= 3)
            )
        ).all()
        if not rows:
            return RelatedSkills(skill=skill_name, base_postings=base_count, items=[])

        totals = dict(
            (
                await self.session.exec(
                    select(PostingSkill.skill_id, func.count(func.distinct(PostingSkill.posting_id)))
                    .where(
                        PostingSkill.skill_id.in_([row[0] for row in rows]),
                        PostingSkill.requirement.in_(grades),
                    )
                    .group_by(PostingSkill.skill_id)
                )
            ).all()
        )

        scored: list[tuple[str, int, float]] = []
        p_x = base_count / universe
        for other_id, name, together in rows:
            other_total = totals.get(other_id, 0)
            if not other_total:
                continue
            p_xy = together / universe
            p_y = other_total / universe
            denominator = -math.log(p_xy)
            npmi = 0.0 if denominator == 0 else math.log(p_xy / (p_x * p_y)) / denominator
            scored.append((name, together, npmi))

        scored.sort(key=lambda row: (-row[2], -row[1], row[0]))
        return RelatedSkills(
            skill=skill_name,
            base_postings=base_count,
            items=[
                RelatedSkill(rank=index, skill=name, cooccurrence=count, npmi=round(npmi, 4))
                for index, (name, count, npmi) in enumerate(scored[:top], start=1)
            ],
        )

    async def _company_top_skills(
        self, company_id: int, grades: tuple[Requirement, ...], top: int
    ) -> tuple[list[SkillCount], int]:
        postings = (
            await self.session.exec(
                select(func.count()).select_from(JobPosting).where(
                    JobPosting.company_id == company_id
                )
            )
        ).one()
        posting_count = func.count(func.distinct(PostingSkill.posting_id))
        rows = (
            await self.session.exec(
                select(Skill.name, posting_count)
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(JobPosting.company_id == company_id, PostingSkill.requirement.in_(grades))
                .group_by(Skill.name)
                .order_by(posting_count.desc(), Skill.name)
                .limit(top)
            )
        ).all()
        skills = [
            SkillCount(
                rank=index,
                skill=name,
                posting_count=count,
                share=round(count / postings, 4) if postings else 0.0,
            )
            for index, (name, count) in enumerate(rows, start=1)
        ]
        return skills, postings

    async def company_profile(self, name: str, top: int = 10) -> CompanyLookup:
        needle = name.strip()
        if not needle:
            return CompanyLookup(status="not_found")

        posting_count = func.count(JobPosting.id)
        matches = (
            await self.session.exec(
                select(Company.id, Company.name, posting_count)
                .outerjoin(JobPosting, JobPosting.company_id == Company.id)
                .where(Company.name.ilike(f"%{needle}%"))
                .group_by(Company.id, Company.name)
                .order_by(posting_count.desc(), Company.name)
                .limit(20)
            )
        ).all()
        if not matches:
            return CompanyLookup(status="not_found")

        exact = [row for row in matches if row[1].strip().lower() == needle.lower()]
        chosen = exact[0] if exact else (matches[0] if len(matches) == 1 else None)
        if chosen is None:
            return CompanyLookup(
                status="ambiguous",
                candidates=[
                    CompanyCandidate(company_id=cid, name=cname, posting_count=count)
                    for cid, cname, count in matches
                ],
            )

        company_id = chosen[0]
        company = (
            await self.session.exec(select(Company).where(Company.id == company_id))
        ).one()
        skills, postings = await self._company_top_skills(company_id, tuple(Requirement), top)

        career = self._career_conditions()
        career_row = (
            await self.session.exec(
                select(
                    *[
                        func.count().filter(condition).label(level)
                        for level, condition in career.items()
                    ]
                )
                .select_from(JobPosting)
                .where(JobPosting.company_id == company_id)
            )
        ).one()

        # 같은 식 객체를 SELECT 와 GROUP BY 에 넘겨야 GroupingError 가 안 난다
        region = func.split_part(JobPosting.location, " ", 1)
        locations = dict(
            (
                await self.session.exec(
                    select(region, func.count())
                    .where(JobPosting.company_id == company_id, JobPosting.location.is_not(None))
                    .group_by(region)
                    .order_by(func.count().desc())
                )
            ).all()
        )

        return CompanyLookup(
            status="found",
            profile=CompanyProfile(
                company_id=company_id,
                name=company.name,
                size_type=str(company.size_type),
                employee_count=company.employee_count,
                industry=company.industry,
                founded=company.founded,
                homepage=company.homepage,
                description=company.description or company.business_content,
                talent_profile=company.talent_profile,
                posting_count=postings,
                top_skills=skills,
                career_distribution=dict(zip(career.keys(), career_row, strict=True)),
                locations=locations,
            ),
        )

    async def compare_companies(
        self, company_ids: Sequence[int], top: int = 10
    ) -> CompanyComparisons:
        if len(company_ids) < 2:
            raise ValueError("비교하려면 기업이 2개 이상이어야 합니다.")

        companies = (
            await self.session.exec(
                select(Company.id, Company.name, Company.size_type).where(
                    Company.id.in_(list(company_ids))
                )
            )
        ).all()

        results: list[CompanyComparison] = []
        skill_sets: list[set[str]] = []
        for company_id, name, size_type in companies:
            skills, postings = await self._company_top_skills(company_id, self._STRICT, top)
            results.append(
                CompanyComparison(
                    company_id=company_id,
                    name=name,
                    size_type=str(size_type),
                    posting_count=postings,
                    top_skills=skills,
                )
            )
            skill_sets.append({item.skill for item in skills})

        shared = sorted(set.intersection(*skill_sets)) if skill_sets else []
        return CompanyComparisons(companies=results, shared_skills=shared)

    async def similar_companies(
        self, company_id: int, top: int = 5
    ) -> Optional[SimilarCompanies]:
        has_text = func.coalesce(
            func.nullif(func.btrim(Company.description), ""),
            func.nullif(func.btrim(Company.business_content), ""),
        ).is_not(None)

        target = (
            await self.session.exec(
                select(
                    Company.name,
                    Company.size_type,
                    Company.employee_count,
                    Company.profile_embedding,
                    has_text,
                ).where(Company.id == company_id)
            )
        ).first()
        if target is None:
            return None
        name, size_type, employees, embedding, target_has_text = target

        target_has_stack, stacks = await self._stack_cosines(company_id)

        described: dict[int, float] = {}
        if target_has_text and embedding is not None:
            distance = Company.profile_embedding.cosine_distance(embedding)
            described = dict(
                (
                    await self.session.exec(
                        select(Company.id, 1 - distance).where(
                            has_text,
                            Company.profile_embedding.is_not(None),
                            Company.id != company_id,
                        )
                    )
                ).all()
            )

        candidates = set(stacks) | set(described)
        if not candidates:
            return SimilarCompanies(company_id=company_id, name=name, items=[])

        meta = (
            await self.session.exec(
                select(Company.id, Company.name, Company.size_type, Company.employee_count).where(
                    Company.id.in_(candidates)
                )
            )
        ).all()

        percentiles = self._percentile_ranks(described)
        scored = []
        for other_id, other_name, other_size, other_employees in meta:
            stack, shared = stacks.get(other_id, (0.0 if target_has_stack else None, []))
            percentile = percentiles.get(other_id)
            description_score = None
            if described:
                description_score = 0.5 if percentile is None else percentile
            size = self._size_proximity(employees, size_type, other_employees, other_size)
            score = self._combine(stack, description_score, size)
            scored.append(
                (
                    score,
                    stack,
                    described.get(other_id),
                    percentile,
                    size,
                    other_id,
                    other_name,
                    other_size,
                    shared,
                )
            )
        scored.sort(key=lambda r: (-r[0], -(r[1] or 0), r[6]))

        def rounded(value: Optional[float]) -> Optional[float]:
            return round(value, 4) if value is not None else None

        return SimilarCompanies(
            company_id=company_id,
            name=name,
            items=[
                SimilarCompany(
                    rank=index,
                    company_id=other_id,
                    name=other_name,
                    size_type=str(other_size),
                    score=round(score, 4),
                    stack_cosine=rounded(stack),
                    description_cosine=rounded(description),
                    description_percentile=rounded(percentile),
                    size_proximity=rounded(size),
                    shared_skills=shared,
                )
                for index, (
                    score,
                    stack,
                    description,
                    percentile,
                    size,
                    other_id,
                    other_name,
                    other_size,
                    shared,
                ) in enumerate(scored[:top], start=1)
            ],
        )

    @staticmethod
    def _percentile_ranks(values: dict[int, float]) -> dict[int, float]:
        # 설명 코사인은 좁은 띠에 몰려 있어 원값 대신 후보 안 백분위로 쓴다
        ordered = sorted(values.values())
        span = max(len(ordered) - 1, 1)
        return {key: bisect_left(ordered, value) / span for key, value in values.items()}

    @staticmethod
    def _size_proximity(
        a_employees: Optional[int],
        a_size: Optional[str],
        b_employees: Optional[int],
        b_size: Optional[str],
    ) -> Optional[float]:
        if a_employees and b_employees:
            # 직원 수 10배 차이면 0.67, 1000배면 0
            return max(0.0, 1 - abs(math.log10(a_employees) - math.log10(b_employees)) / 3)
        order = ["startup", "small", "medium", "large", "enterprise"]
        if a_size not in order or b_size not in order:
            return None
        return 1 - abs(order.index(a_size) - order.index(b_size)) / 4

    @staticmethod
    def _combine(stack: Optional[float], description: Optional[float], size: Optional[float]) -> float:
        parts = [(w, v) for w, v in ((0.5, stack), (0.35, description), (0.15, size)) if v is not None]
        total = sum(w for w, _ in parts)
        return sum(w * v for w, v in parts) / total if total else 0.0

    async def _stack_cosines(
        self, company_id: int
    ) -> tuple[bool, dict[int, tuple[float, list[str]]]]:
        counts = (
            select(
                JobPosting.company_id.label("company_id"),
                PostingSkill.skill_id.label("skill_id"),
                func.count(func.distinct(PostingSkill.posting_id)).label("n"),
            )
            .select_from(PostingSkill)
            .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
            .join(Skill, Skill.id == PostingSkill.skill_id)
            .where(
                JobPosting.company_id.is_not(None),
                PostingSkill.requirement.in_(self._DEMAND),
                Skill.is_common.is_(False),
            )
            .group_by(JobPosting.company_id, PostingSkill.skill_id)
            .cte("counts")
        )
        target = (
            select(counts.c.skill_id, counts.c.n)
            .where(counts.c.company_id == company_id)
            .cte("target")
        )
        norms = (
            select(counts.c.company_id, func.sqrt(func.sum(counts.c.n * counts.c.n)).label("norm"))
            .group_by(counts.c.company_id)
            .cte("norms")
        )

        target_norm = (
            await self.session.exec(select(func.sqrt(func.sum(target.c.n * target.c.n))))
        ).one()
        if not target_norm:
            return False, {}

        contribution = counts.c.n * target.c.n
        rows = (
            await self.session.exec(
                select(
                    counts.c.company_id,
                    func.sum(contribution) / norms.c.norm,
                    func.array_agg(aggregate_order_by(Skill.name, contribution.desc())),
                )
                .select_from(counts)
                .join(target, target.c.skill_id == counts.c.skill_id)
                .join(norms, norms.c.company_id == counts.c.company_id)
                .join(Skill, Skill.id == counts.c.skill_id)
                .where(counts.c.company_id != company_id)
                .group_by(counts.c.company_id, norms.c.norm)
            )
        ).all()
        return True, {
            other_id: (float(dot) / float(target_norm), list(names[:5]))
            for other_id, dot, names in rows
        }

    async def search_companies(
        self,
        query_vec: Sequence[float],
        size_type: Optional[CompanySize] = None,
        field: Optional[TechFieldCode] = None,
        top: int = 10,
    ) -> list[CompanyHit]:
        await self.session.exec(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

        filters: list[Any] = [Company.profile_embedding.is_not(None)]
        if size_type is not None:
            filters.append(Company.size_type == size_type)
        if field is not None:
            filters.append(
                Company.id.in_(
                    select(JobPosting.company_id).where(
                        *self._posting_filters(
                            field=field, size_type=None, career_level=None, days=None
                        )
                    )
                )
            )

        postings = (
            select(func.count())
            .select_from(JobPosting)
            .where(JobPosting.company_id == Company.id)
            .correlate(Company)
            .scalar_subquery()
        )
        evidence = func.left(
            func.coalesce(Company.description, Company.business_content, Company.industry),
            200,
        )
        distance = Company.profile_embedding.cosine_distance(list(query_vec))
        rows = (
            await self.session.exec(
                select(
                    Company.id,
                    Company.name,
                    Company.size_type,
                    Company.industry,
                    postings,
                    evidence,
                    distance,
                )
                .where(*filters)
                .order_by(distance)
                .limit(top)
            )
        ).all()

        return [
            CompanyHit(
                company_id=company_id,
                name=name,
                size_type=str(size),
                industry=industry,
                posting_count=count,
                evidence=snippet,
                similarity=round(1 - d, 4),
            )
            for company_id, name, size, industry, count, snippet, d in sorted(
                rows, key=lambda r: r[6]
            )
        ]

    async def search_postings(
        self,
        query_vec: Sequence[float],
        field: Optional[TechFieldCode] = None,
        career_max: Optional[int] = None,
        section: Optional[ChunkSection] = None,
        top: int = 10,
        active_only: bool = True,
    ) -> list[PostingHit]:
        await self.session.exec(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

        filters = self._posting_filters(field=field, size_type=None, career_level=None, days=None)
        if career_max is not None:
            filters.append(func.coalesce(JobPosting.career_min, 0) <= career_max)
        if section is not None:
            filters.append(PostingChunk.section == section)
        if active_only:
            filters.append(JobPosting.expires_at.is_(None) | (JobPosting.expires_at >= func.now()))

        distance = PostingChunk.embedding.cosine_distance(list(query_vec))
        rows = (
            await self.session.exec(
                select(
                    JobPosting.id,
                    JobPosting.title,
                    func.coalesce(Company.name, JobPosting.company_name_raw),
                    JobPosting.raw_fields["url"].astext,
                    PostingChunk.section,
                    PostingChunk.content,
                    distance,
                )
                .select_from(PostingChunk)
                .join(JobPosting, JobPosting.id == PostingChunk.posting_id)
                .outerjoin(Company, Company.id == JobPosting.company_id)
                .where(*filters)
                .order_by(distance)
                .limit(top * 4)
            )
        ).all()

        # relaxed_order 는 순서가 어긋날 수 있어 다시 정렬한다
        best: dict[tuple[Optional[str], str], Any] = {}
        for row in sorted(rows, key=lambda r: r[6]):
            best.setdefault((row[2], row[1].strip()), row)
        return [
            PostingHit(
                posting_id=posting_id,
                title=title,
                company=company,
                url=url,
                section=str(chunk_section),
                chunk=content,
                similarity=round(1 - d, 4),
            )
            for posting_id, title, company, url, chunk_section, content, d in list(best.values())[:top]
        ]

    async def skill_gap(
        self,
        my_skills: Sequence[str],
        field: Optional[TechFieldCode] = None,
        company_ids: Optional[Sequence[int]] = None,
        career_level: Optional[CareerLevel] = None,
        days: Optional[int] = 90,
        top: int = 15,
    ) -> SkillGap:
        matched: list[str] = []
        unknown: list[str] = []
        owned_ids: set[int] = set()
        for raw in my_skills:
            found = await self.resolve_skill_name(raw)
            if found is None:
                unknown.append(raw)
            else:
                owned_ids.add(found[0])
                matched.append(found[1])

        filters = self._posting_filters(
            field=field, size_type=None, career_level=career_level, days=days
        )
        if company_ids:
            filters.append(JobPosting.company_id.in_(list(company_ids)))
        filters.append(PostingSkill.requirement.in_(self._STRICT))
        filters.append(Skill.is_common.is_(False))

        analyzed = (
            await self.session.exec(
                select(func.count(func.distinct(PostingSkill.posting_id)))
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*filters)
            )
        ).one()

        posting_count = func.count(func.distinct(PostingSkill.posting_id))
        rows = (
            await self.session.exec(
                select(Skill.id, Skill.name, posting_count)
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*filters)
                .group_by(Skill.id, Skill.name)
                .order_by(posting_count.desc(), Skill.name)
                .limit(top + len(owned_ids))
            )
        ).all()

        demanded_total = sum(count for _, _, count in rows)
        covered = sum(count for skill_id, _, count in rows if skill_id in owned_ids)
        missing = [
            SkillGapItem(
                rank=index,
                skill=name,
                posting_count=count,
                share=round(count / analyzed, 4) if analyzed else 0.0,
            )
            for index, (skill_id, name, count) in enumerate(
                (row for row in rows if row[0] not in owned_ids), start=1
            )
        ][:top]

        return SkillGap(
            matched=matched,
            unknown_inputs=unknown,
            missing=missing,
            analyzed_postings=analyzed,
            coverage=round(covered / demanded_total, 4) if demanded_total else 0.0,
        )
