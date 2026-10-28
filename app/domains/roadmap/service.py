import asyncio
import logging

from itertools import islice
from typing import Any, Iterable, Iterator, Optional

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exception.exceptions import AppException
from app.domains.roadmap import prompt
from app.domains.roadmap.enums import RoadmapStatus
from app.domains.roadmap.exceptions import (
    RoadmapGenerationFailed,
    RoadmapNotFound,
)
from app.domains.roadmap.repository import RoadmapRepository
from app.domains.roadmap.schemas import (
    RoadmapFacts,
    RoadmapOutline,
    RoadmapWeek,
)
from app.infra.llm.tracing import trace_config


log = logging.getLogger(__name__)


class RoadmapService:
    CONCURRENCY = 4
    LLM_RETRY = 1

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        model: BaseChatModel,
    ):
        self.session_factory = session_factory
        self.model = model


    async def roadmap_generator(
        self,
        roadmap_id: int,
        additional_comment: Optional[str] = None,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            repo = RoadmapRepository(session)

            roadmap = await repo.get(roadmap_id)
            if roadmap is None:
                raise RoadmapNotFound()

            if roadmap.status == RoadmapStatus.GENERATING:
                log.info("이미 생성 중입니다 id=%s", roadmap_id)
                return {"roadmap_id": roadmap_id, "skipped": True}

            target_weeks = roadmap.target_weeks
            user_id = roadmap.user_id
            field_id = roadmap.field_id
            skill_ids = list(roadmap.skill_ids or [])

            await repo.mark_generating(roadmap_id)
            await session.commit()

        try:
            async with self.session_factory() as session:
                facts = await RoadmapRepository(session).collect(
                    field_id=field_id,
                    skill_ids=skill_ids,
                )

            outline = await self._outline(
                roadmap_id,
                user_id,
                facts,
                target_weeks,
                additional_comment
            )

            async with self.session_factory() as session:
                await RoadmapRepository(session).save_outline(roadmap_id, outline)
                await session.commit()

            items = await self._weeks(
                roadmap_id,
                user_id,
                facts,
                outline,
                target_weeks,
                additional_comment
            )

            async with self.session_factory() as session:
                await RoadmapRepository(session).mark_completed(roadmap_id)
                await session.commit()

        except BaseException as exc:
            log.exception(
                "로드맵 생성 실패 id=%s detail=%s",
                roadmap_id, getattr(exc, "detail", None)
            )
            message = (
                exc.message if isinstance(exc, AppException)
                else "로드맵 생성 중 오류가 발생했습니다."
            )

            async with self.session_factory() as session:
                await RoadmapRepository(session).mark_failed(roadmap_id, message)
                await session.commit()
            raise

        log.info(
            "로드맵 생성 완료 id=%s %d주 %d항목",
            roadmap_id, target_weeks, items
        )

        return {
            "roadmap_id": roadmap_id,
            "weeks": target_weeks,
            "items": items,
        }


    async def fail_stale(self, older_than_seconds: int) -> int:
        async with self.session_factory() as session:
            count = await RoadmapRepository(session).fail_stale(older_than_seconds)
            await session.commit()

        if count:
            log.warning("마감되지 못한 로드맵 %d건을 failed 로 정리했습니다.", count)

        return count


    async def _outline(
        self,
        roadmap_id: int,
        user_id: int,
        facts: RoadmapFacts,
        target_weeks: int,
        comment: Optional[str],
    ) -> RoadmapOutline:
        outline_model = self.model.with_structured_output(RoadmapOutline)
        expected = list(range(1, target_weeks + 1))

        for attempt in range(self.LLM_RETRY + 1):
            try:
                outline = await outline_model.ainvoke(
                    [
                        ("system", prompt.OUTLINE_SYSTEM),
                        ("human", prompt.build_outline(facts, target_weeks, comment)),
                    ],
                    config=self._trace(
                        "roadmap_outline",
                        roadmap_id,
                        user_id,
                        {"target_weeks": target_weeks, "attempt": attempt + 1},
                    ),
                )

                if sorted(w.week for w in outline.weeks) != expected:
                    raise RoadmapGenerationFailed(
                        f"뼈대 주차 불일치 {len(outline.weeks)}/{target_weeks}"
                    )

                return outline

            except Exception as exc:
                log.warning("뼈대 생성 실패 (%d회차) %r", attempt + 1, exc)

        raise RoadmapGenerationFailed("뼈대 재시도 소진")


    async def _weeks(
        self,
        roadmap_id: int,
        user_id: int,
        facts: RoadmapFacts,
        outline: RoadmapOutline,
        target_weeks: int,
        comment: Optional[str],
    ) -> int:
        week_model = self.model.with_structured_output(RoadmapWeek)
        saved = 0

        for batch in self._batched(range(1, target_weeks + 1), self.CONCURRENCY):
            weeks = await self._run_batch(
                week_model,
                roadmap_id,
                user_id,
                facts,
                outline,
                batch,
                comment
            )

            async with self.session_factory() as session:
                repo = RoadmapRepository(session)
                saved += await repo.save_items(roadmap_id, weeks)
                await repo.bump_generated(roadmap_id, len(weeks))
                await session.commit()

        return saved


    async def _run_batch(
        self,
        week_model: Any,
        roadmap_id: int,
        user_id: int,
        facts: RoadmapFacts,
        outline: RoadmapOutline,
        batch: list[int],
        comment: Optional[str],
    ) -> list[RoadmapWeek]:
        pending = list(batch)
        done: list[RoadmapWeek] = []

        for attempt in range(self.LLM_RETRY + 1):
            results = await asyncio.gather(
                *[
                    self._one_week(
                        week_model,
                        facts,
                        outline,
                        week_no,
                        comment,
                        self._trace(
                            "roadmap_week",
                            roadmap_id,
                            user_id,
                            {"week": week_no, "attempt": attempt + 1},
                        ),
                    )
                    for week_no in pending
                ],
                return_exceptions=True,
            )

            failed: list[int] = []
            for week_no, result in zip(pending, results):
                if isinstance(result, Exception):
                    log.warning("%d주차 생성 실패 (%d회차) %r", week_no, attempt + 1, result)
                    failed.append(week_no)
                else:
                    done.append(result)

            if not failed:
                return done

            pending = failed

        raise RoadmapGenerationFailed(f"{pending}주차 재시도 소진")


    @staticmethod
    async def _one_week(
        week_model: Any,
        facts: RoadmapFacts,
        outline: RoadmapOutline,
        week_no: int,
        comment: Optional[str],
        config: dict[str, Any],
    ) -> RoadmapWeek:
        week = await week_model.ainvoke(
            [
                ("system", prompt.WEEK_SYSTEM),
                ("human", prompt.build_week(facts, outline, week_no, comment)),
            ],
            config=config,
        )

        if len(week.days) != 7:
            raise RoadmapGenerationFailed(f"{week_no}주차 일수 {len(week.days)}")

        first_day = (week_no - 1) * 7 + 1
        for offset, day in enumerate(week.days):
            day.day = first_day + offset

        week.week = week_no
        return week


    @staticmethod
    def _trace(
        name: str,
        roadmap_id: int,
        user_id: int,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return trace_config(
            name,
            session_id=f"roadmap-{roadmap_id}",
            user_id=str(user_id),
            tags=["roadmap"],
            metadata={"roadmap_id": roadmap_id, **metadata},
        )


    @staticmethod
    def _batched(values: Iterable[int], size: int) -> Iterator[list[int]]:
        iterator = iter(values)
        while chunk := list(islice(iterator, size)):
            yield chunk