from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import aliased
from sqlmodel import and_, func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.crawler.enums import Requirement
from app.domains.crawler.models import (
    JobPosting,
    PostingSkill,
    Skill,
    SkillField,
    TechField,
)
from app.domains.roadmap.enums import RoadmapStatus
from app.domains.roadmap.model import Roadmap, RoadmapItem
from app.domains.roadmap.schemas import (
    RoadmapOutline,
    RoadmapWeek,
    RoadmapFacts,
    SkillDemand
)


class RoadmapRepository:
    WINDOW_DAYS = 90
    DEMAND_TOP = 15
    COMPANION_TOP = 5
    GAP_TOP = 10

    _DEMAND = (
        Requirement.TAG,
        Requirement.REQUIRED,
        Requirement.PREFERRED
    )

    def __init__(self, session: AsyncSession):
        self.session = session


    async def get(self, roadmap_id: int) -> Optional[Roadmap]:
        rows = await self.session.exec(
            select(Roadmap).where(Roadmap.id == roadmap_id)
        )

        return rows.first()


    async def mark_generating(self, roadmap_id: int):
        await self.session.exec(
            update(Roadmap)
            .where(Roadmap.id == roadmap_id)
            .values(
                status=RoadmapStatus.GENERATING,
                generated_weeks=0,
                error_message=None,
                completed_at=None,
            )
        )


    async def save_outline(self, roadmap_id: int, outline: RoadmapOutline):
        await self.session.exec(
            update(Roadmap)
            .where(Roadmap.id == roadmap_id)
            .values(outline=outline.model_dump(mode="json"))
        )


    async def save_items(self, roadmap_id: int, weeks: list[RoadmapWeek]) -> int:
        now = datetime.now(UTC)
        rows = [
            {
                "roadmap_id": roadmap_id,
                "week_no": week.week,
                "day_no": day.day,
                "seq": seq,
                "title": item.title[:200],
                "description": item.description,
                "created_at": now,
            }
            for week in weeks
            for day in week.days
            for seq, item in enumerate(day.items, start=1)
        ]

        if not rows:
            return 0

        stmt = pg_insert(RoadmapItem).values(rows)
        await self.session.exec(
            stmt.on_conflict_do_update(
                constraint="uk_roadmap_item",
                set_={
                    "title": stmt.excluded.title,
                    "description": stmt.excluded.description,
                },
            )
        )

        return len(rows)


    async def bump_generated(self, roadmap_id: int, count: int):
        await self.session.exec(
            update(Roadmap)
            .where(Roadmap.id == roadmap_id)
            .values(generated_weeks=Roadmap.generated_weeks + count)
        )


    async def mark_completed(self, roadmap_id: int):
        await self.session.exec(
            update(Roadmap)
            .where(Roadmap.id == roadmap_id)
            .values(
                status=RoadmapStatus.COMPLETED,
                completed_at=datetime.now(UTC),
                error_message=None,
            )
        )


    async def mark_failed(self, roadmap_id: int, message: str):
        await self.session.exec(
            update(Roadmap)
            .where(Roadmap.id == roadmap_id)
            .values(status=RoadmapStatus.FAILED, error_message=message)
        )


    async def collect(
        self,
        field_id: Optional[int],
        skill_ids: list[int],
    ):
        field_name = await self._field_name(field_id)
        user_skills = await self._skill_names(skill_ids)

        total = await self._total_postings(field_id)
        demand = await self._demand(field_id, total)
        companions = await self._companions(field_id, skill_ids)

        chosen = set(user_skills)
        gaps = [d for d in demand if d.name not in chosen][: self.GAP_TOP]

        return RoadmapFacts(
            field_name=field_name,
            window_days=self.WINDOW_DAYS,
            total_postings=total,
            user_skills=user_skills,
            demand=demand,
            companions=companions,
            gaps=gaps,
        )


    async def fail_stale(self, older_than_seconds: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        result = await self.session.exec(
            update(Roadmap)
            .where(
                Roadmap.status.in_((RoadmapStatus.PENDING, RoadmapStatus.GENERATING)),
                Roadmap.created_at < cutoff,
            )
            .values(status=RoadmapStatus.FAILED, error_message="생성이 완료되지 않았습니다.")
        )
        return result.rowcount


    def _posting_filters(self, field_id: Optional[int]) -> list[Any]:
        conditions: list[Any] = [
            JobPosting.posted_at >=
            (datetime.now(UTC) - timedelta(days=self.WINDOW_DAYS))
        ]
        if field_id is not None:
            conditions.append(JobPosting.field_id == field_id)
        return conditions


    async def _field_name(self, field_id: Optional[int]) -> str:
        if field_id is None:
            return "전체"

        field_name = (
            await self.session.exec(
                select(TechField.name).where(TechField.id == field_id)
            )
        ).first()

        return field_name or "전체"


    async def _skill_names(self, skill_ids: list[int]) -> list[str]:
        if not skill_ids:
            return []

        skill_names = (
            await self.session.exec(
                select(Skill.name)
                .where(Skill.id.in_(skill_ids))
                .order_by(Skill.name)
            )
        ).all()

        return list(skill_names)


    async def _total_postings(self, field_id: Optional[int]) -> int:
        postings = (
                await self.session.exec(
                select(func.count())
                .select_from(JobPosting)
                .where(*self._posting_filters(field_id))
            )
        ).one()

        return postings


    async def _demand(self, field_id: Optional[int], total: int) -> list[SkillDemand]:
        posting_count = func.count(func.distinct(PostingSkill.posting_id))

        stmt = (
            select(Skill.name, posting_count)
            .select_from(PostingSkill)
            .join(Skill, Skill.id == PostingSkill.skill_id)
            .join(JobPosting, JobPosting.id == PostingSkill.posting_id)
            .where(
                *self._posting_filters(field_id),
                PostingSkill.requirement.in_(self._DEMAND),
                Skill.is_common.is_(False),
            )
            .group_by(Skill.name)
            .order_by(posting_count.desc())
            .limit(self.DEMAND_TOP)
        )

        if field_id is not None:
            stmt = stmt.join(
                SkillField,
                and_(
                    SkillField.skill_id == Skill.id,
                    SkillField.field_id == field_id,
                ),
            )

        rows = (await self.session.exec(stmt)).all()

        return [
            SkillDemand(
                name=name,
                posting_count=count,
                share=round(count / total, 4) if total else 0.0,
            )
            for name, count in rows
        ]


    async def _companions(
        self, field_id: Optional[int], skill_ids: list[int]
    ) -> dict[str, list[str]]:
        if not skill_ids:
            return {}

        anchor = aliased(PostingSkill)
        other = aliased(PostingSkill)
        anchor_skill = aliased(Skill)

        posting_count = func.count(func.distinct(JobPosting.id))

        rows = (
            await self.session.exec(
                select(anchor_skill.name, Skill.name, posting_count)
                .select_from(JobPosting)
                .join(anchor, anchor.posting_id == JobPosting.id)
                .join(anchor_skill, anchor_skill.id == anchor.skill_id)
                .join(other, other.posting_id == JobPosting.id)
                .join(Skill, Skill.id == other.skill_id)
                .where(
                    *self._posting_filters(field_id),
                    anchor.skill_id.in_(skill_ids),
                    other.skill_id.notin_(skill_ids),
                    anchor.requirement.in_(self._DEMAND),
                    other.requirement.in_(self._DEMAND),
                    Skill.is_common.is_(False),
                )
                .group_by(anchor_skill.name, Skill.name)
                .order_by(anchor_skill.name, posting_count.desc())
            )
        ).all()

        result: dict[str, list[str]] = {}
        for anchor_name, companion, _count in rows:
            bucket = result.setdefault(anchor_name, [])
            if len(bucket) < self.COMPANION_TOP:
                bucket.append(companion)

        return result