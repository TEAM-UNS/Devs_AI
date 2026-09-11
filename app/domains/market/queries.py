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
    resolve_skill(query_vec)                         skill.embedding 코사인

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

import math
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

from app.domains.market import similarity
from app.domains.market.enums import (
    CareerLevel,
    ChunkSection,
    CompanySize,
    Requirement,
    RunKind,
    SalaryType,
)

from app.domains.market.enums import TechField as TechFieldCode
from app.domains.market.models import (
    Company,
    CrawlRun,
    JobPosting,
    PostingChunk,
    PostingSkill,
    Skill,
    TechField,
    SkillAlias,
)
from app.domains.market.schemas import (
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


# 공용 표현식 
APPEARED_AT = func.coalesce(JobPosting.posted_at, JobPosting.created_at)

_DISCLOSED = (SalaryType.RANGE, SalaryType.MIN_ONLY, SalaryType.MAX_ONLY)

_DEMAND = (Requirement.TAG, Requirement.REQUIRED, Requirement.PREFERRED)

# "실제로 요구되는 것". 우대사항까지 빼고 필수만 본다. 학습 우선순위·기업 비교용.
_STRICT = (Requirement.TAG, Requirement.REQUIRED)

# 증감률 = (recent + K) / (previous + K) - 1
# ★ K 없이 나누면 지난 구간 0건이던 스킬이 이번에 1건만 나와도 증가율이 무한대가
#   되어 상위권을 잡음이 채운다. K=1 은 "1건은 우연일 수 있다" 는 최소한의 스무딩.
_RISING_SMOOTHING = 1
_RISING_WINDOW_DAYS = 7
_RISING_MIN_COUNT = 5

# NPMI 계산에 넣을 최소 동시출현 횟수. 1~2회 함께 나온 쌍은 희소할수록 PMI 가
# 커지는 성질 때문에 높게 계산되지만 실제로는 우연이다.
_NPMI_MIN_COOCCURRENCE = 3

# 공고당 청크가 ~3개라, 공고 top 개를 채우려면 청크를 넉넉히 뽑아 중복을 걷어낸다.
_SEARCH_CHUNKS_PER_POSTING = 4

_EVIDENCE_CHARS = 200

_SHARED_SKILLS = 5

# 집계에 들어간 표본이 이보다 적으면 low_confidence 를 실어 보낸다. 툴 결과를
# 읽는 것은 사람이 아니라 LLM 이라, 근거를 주지 않으면 표본 3건짜리 중앙값도
# 단정적으로 말한다.
_LOW_CONFIDENCE_SAMPLE = 10

# salary_min/max 의 단위. 저장 시 연봉 만원으로 통일한다.
_SALARY_UNIT = "만원"

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
        # 사전 일치(resolve_skill_name)가 실패했을 때의 후보 제시용. 165행이라 인덱스 없이 스캔한다.
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

        base = [PostingSkill.skill_id == skill_id, PostingSkill.requirement.in_(_DEMAND)]
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
            low, high = _CAREER_RANGES[level]
            parts = [or_(JobPosting.career_max.is_(None), JobPosting.career_max >= low)]
            if high is not None:
                parts.append(func.coalesce(JobPosting.career_min, 0) <= high)
            conditions[level.value] = and_(*parts)
        return conditions
    # ── 급상승 기술 ────────────────────────────────────────────────────────
    async def rising_skills(
        self,
        field: Optional[TechFieldCode] = None,
        window_days: int = _RISING_WINDOW_DAYS,
        min_count: int = _RISING_MIN_COUNT,
        top: int = 10,
        include_common: bool = False,
    ) -> RisingSkills:
        """이번 N일 vs 직전 N일 비교. 증감률 = (recent+1)/(previous+1)-1."""
        now = datetime.now(UTC)
        recent_from = now - timedelta(days=window_days)
        previous_from = now - timedelta(days=window_days * 2)

        base = self._posting_filters(field=field, size_type=None, career_level=None, days=None)
        base.append(PostingSkill.requirement.in_(_DEMAND))
        if not include_common:
            base.append(Skill.is_common.is_(False))

        recent = func.count(func.distinct(PostingSkill.posting_id)).filter(
            APPEARED_AT >= recent_from
        )
        previous = func.count(func.distinct(PostingSkill.posting_id)).filter(
            and_(APPEARED_AT >= previous_from, APPEARED_AT < recent_from)
        )

        rows = (
            await self.session.exec(
                select(Skill.name, recent, previous)
                .select_from(PostingSkill)
                .join(Skill, Skill.id == PostingSkill.skill_id)
                .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
                .where(*base, APPEARED_AT >= previous_from)
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
                    (recent_count + _RISING_SMOOTHING) / (previous_count + _RISING_SMOOTHING) - 1,
                )
                for name, recent_count, previous_count in rows
            ),
            key=lambda row: (-row[3], row[0]),
        )[:top]

        window_counts = (
            await self.session.exec(
                select(
                    func.count().filter(APPEARED_AT >= recent_from),
                    func.count().filter(
                        and_(APPEARED_AT >= previous_from, APPEARED_AT < recent_from)
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
            # ★ 어느 한쪽 구간이라도 표본이 얇으면 증감률을 믿을 수 없다.
            low_confidence=min(recent_postings, previous_postings) < _LOW_CONFIDENCE_SAMPLE * 10,
        )

    # ── 세그먼트별 스택 ────────────────────────────────────────────────────
    async def stacks_by_segment(
        self,
        group_by: str = "size",
        field: Optional[TechFieldCode] = None,
        top: int = 10,
        days: Optional[int] = 30,
        include_common: bool = False,
    ) -> StacksBySegment:
        """size · career · location 세그먼트마다 상위 스킬을 뽑는다."""
        if group_by not in ("size", "career", "location"):
            raise ValueError(f"group_by 는 size · career · location 중 하나여야 합니다: {group_by}")

        base = self._posting_filters(
            field=field, size_type=None, career_level=None, days=days
        )
        base.append(PostingSkill.requirement.in_(_DEMAND))
        if not include_common:
            base.append(Skill.is_common.is_(False))

        if group_by == "career":
            # 경력은 구간이 겹쳐 GROUP BY 로 못 나눈다. 구간마다 따로 집계한다.
            segments = []
            for level, condition in self._career_conditions().items():
                segments.append(await self._segment_stacks(level, base + [condition], top))
            return StacksBySegment(group_by=group_by, segments=segments)

        if group_by == "size":
            label = Company.size_type
            extra_join = (Company, Company.id == JobPosting.company_id)
        else:
            # "서울 강남구" → "서울". 구 단위로 쪼개면 세그먼트가 너무 잘게 나뉜다.
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

    # ── 연봉 통계 ──────────────────────────────────────────────────────────
    async def salary_stats(
        self,
        field: Optional[TechFieldCode] = None,
        size_type: Optional[CompanySize] = None,
        career_level: Optional[CareerLevel] = None,
        company_id: Optional[int] = None,
        skill: Optional[str] = None,
        days: Optional[int] = None,
    ) -> SalaryStats:
        """중앙값·사분위 + 공개율.

        ★ 공개율의 분모는 집계 대상이 아니라 필터를 통과한 **전체 공고수** 다.
          집계 대상을 분모로 쓰면 공개율이 항상 1.0 이 된다.
        """
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
                            PostingSkill.requirement.in_(_DEMAND),
                        )
                    )
                )

        # ★ max_only 는 집계에서 뺀다. salary_min 이 없어 대푯값을 정할 수 없고,
        #   salary_max 로 세면 "이하" 를 상한값 그 자체로 취급해 통계가 위로 끌린다.
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

        # breakdown 은 SalaryType 전체를 실어야 합계가 total 과 맞는다.
        breakdown = dict(
            (
                await self.session.exec(
                    select(JobPosting.salary_type, func.count())
                    .where(*filters)
                    .group_by(JobPosting.salary_type)
                )
            ).all()
        )

        def to_int(value: Any) -> int | None:
            return int(value) if value is not None else None

        return SalaryStats(
            sample_size=sample,
            total_postings=total,
            disclosure_rate=round(sample / total, 4) if total else 0.0,
            unit=_SALARY_UNIT,
            median=to_int(median),
            q1=to_int(q1),
            q3=to_int(q3),
            minimum=to_int(minimum),
            maximum=to_int(maximum),
            breakdown={str(k): v for k, v in breakdown.items()},
            low_confidence=sample < _LOW_CONFIDENCE_SAMPLE,
        )

    # ── 연관 기술 ──────────────────────────────────────────────────────────
    async def related_skills(
        self,
        skill_query: str,
        field: Optional[TechFieldCode] = None,
        requirement: Optional[Requirement] = None,
        top: int = 10,
        days: Optional[int] = None,
    ) -> Optional[RelatedSkills]:
        """동시출현 + NPMI. 함께 요구되는 기술을 찾는다.

        NPMI = ln(p(x,y) / (p(x)p(y))) / -ln(p(x,y))     범위 [-1, 1]
        단순 동시출현수만 쓰면 흔한 스킬(Python·AWS)이 무조건 상위를 차지한다.
        """
        found = await self.resolve_skill_name(skill_query)
        if found is None:
            return None
        skill_id, skill_name = found

        grades = (requirement,) if requirement is not None else _DEMAND
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

        # 기준 스킬이 등장한 공고
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
                .having(cooccurrence >= _NPMI_MIN_COOCCURRENCE)
            )
        ).all()
        if not rows:
            return RelatedSkills(skill=skill_name, base_postings=base_count, items=[])

        # 상대 스킬의 전체 등장 횟수 (NPMI 의 p(y))
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

    # ── 기업 ───────────────────────────────────────────────────────────────
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
        """기업 프로필. 동명·유사명이 여럿이면 후보 목록을 돌려준다.

        요구 스택은 body 를 포함한 전 등급을 본다 — "우리는 AWS 위에서 운영합니다"
        는 요구사항은 아니지만 그 회사의 기술 정보로는 유효하다.
        """
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

        # ★ 같은 식 객체를 SELECT · GROUP BY 에 함께 넘겨야 한다. 따로 만들면
        #   SQLAlchemy 가 다른 식으로 보고 GroupingError 가 난다.
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
        """기업 2~5개의 요구 스택을 나란히 놓고 공통 스킬을 뽑는다."""
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
            # 기업 비교는 필수 요구만 본다. 우대사항까지 넣으면 차이가 흐려진다.
            skills, postings = await self._company_top_skills(company_id, _STRICT, top)
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

    # ── 유사 기업 ──────────────────────────────────────────────────────────
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

        percentiles = similarity.percentile_ranks(described)
        scored = []
        for other_id, other_name, other_size, other_employees in meta:
            stack, shared = stacks.get(other_id, (0.0 if target_has_stack else None, []))
            percentile = percentiles.get(other_id)
            # 소개글 없는 후보는 중립값. 기준 기업에 소개글이 없으면 설명 항목 자체를 뺀다.
            description_score = None
            if described:
                description_score = similarity.NEUTRAL if percentile is None else percentile
            size = similarity.size_proximity(employees, size_type, other_employees, other_size)
            score = similarity.combine(stack, description_score, size)
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

        def rounded(value: float | None) -> float | None:
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

    async def _stack_cosines(
        self, company_id: int
    ) -> tuple[bool, dict[int, tuple[float, list[str]]]]:
        """기업별 요구 스킬 공고수 벡터의 코사인. 기준 기업과 스킬이 하나라도 겹치는 기업만."""
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
                PostingSkill.requirement.in_(_DEMAND),
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
            other_id: (float(dot) / float(target_norm), list(names[:_SHARED_SKILLS]))
            for other_id, dot, names in rows
        }

    # ── 기업 검색 ──────────────────────────────────────────────────────────
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
            # 그 분야 공고를 한 건이라도 낸 기업
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
            _EVIDENCE_CHARS,
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

    # ── 공고 검색 ──────────────────────────────────────────────────────────
    async def search_postings(
        self,
        query_vec: Sequence[float],
        field: Optional[TechFieldCode] = None,
        career_max: Optional[int] = None,
        section: Optional[ChunkSection] = None,
        top: int = 10,
        active_only: bool = True,
    ) -> list[PostingHit]:
        # 필터가 있으면 HNSW 가 ef_search(40)개만 보고 걸러 top 보다 적게 줄 수 있다.
        # iterative_scan 은 모자라면 인덱스를 더 읽는다 (pgvector 0.8+).
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
                .limit(top * _SEARCH_CHUNKS_PER_POSTING)
            )
        ).all()

        # relaxed_order 는 순서가 약간 어긋날 수 있어 다시 정렬한다.
        # 같은 공고를 여러 사이트가 올린 경우가 있어 (회사, 제목) 으로 걷어낸다.
        best: dict[tuple[str | None, str], Any] = {}
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

    # ── 갭 분석 ────────────────────────────────────────────────────────────
    async def skill_gap(
        self,
        my_skills: Sequence[str],
        field: Optional[TechFieldCode] = None,
        company_ids: Optional[Sequence[int]] = None,
        career_level: Optional[CareerLevel] = None,
        days: Optional[int] = 90,
        top: int = 15,
    ) -> SkillGap:
        """내 스킬과 시장 요구의 차이.

        ★ requirement 는 required + tag 만 본다. 우대사항까지 "부족" 으로 잡으면
          목록이 무의미해진다 (실측: Kubernetes 는 preferred 가 required 보다 많다).
        ★ "합격 확률" 은 지원 결과 데이터가 없어 계산하지 않는다. coverage 로
          대체하고 "확률" 이라는 표현을 쓰지 않는다.
        """
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
        filters.append(PostingSkill.requirement.in_(_STRICT))
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
