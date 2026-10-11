import logging
from typing import Optional
from datetime import date, timedelta

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import Settings
from app.domains.report import prompt
from app.domains.report.exceptions import (
    ReportDataIncomplete,
    ReportGenerationFailed,
    ReportNotFound,
)
from app.domains.report.models import Report
from app.domains.report.repository import ReportRepository
from app.domains.report.schemas import (
    PopularTechStack,
    ReportFacts,
    TechChange,
    TechMention,
)


log = logging.getLogger(__name__)


settings = Settings()


class ReportService:
    def __init__(
        self,
        repository: ReportRepository,
        model: BaseChatModel,
    ):
        self.repository = repository
        self.model = model


    async def generate_week(
        self, week_start_date:
        date, force: bool
    ) -> dict[str, int]:
        rows = await self.repository.list_by_week(week_start_date)
        if not rows:
            raise ReportNotFound(week_start_date)

        names = await self.repository.field_names()
        counts = {
            "total": len(rows),
            "generated": 0,
            "skipped": 0,
            "retryable": 0,
            "failed": 0
        }

        for row in rows:
            counts[await self._generate_one(row, names, force)] += 1

        log.info(
            "리포트 %s — 총 %d · 생성 %d · 건너뜀 %d · 실패 %d",
            week_start_date, counts["total"],
            counts["generated"], counts["skipped"], counts["failed"],
        )

        return counts


    async def fill_missing(self) -> dict[str, int]:
        since = date.today() - timedelta(weeks=settings.report_backfill_weeks)
        weeks = await self.repository.weeks_with_missing(since)

        total = {
            "weeks": len(weeks),
            "generated": 0,
            "retryable": 0,
            "blocked": 0
        }
        for week in weeks:
            counts = await self.generate_week(week, force=False)
            for key in ("generated", "retryable", "blocked"):
                total[key] += counts[key]

        return total


    async def _generate_one(
        self,
        row: Report,
        names: dict[int, str],
        force: bool,
    ) -> str:
        if row.llm_report and not force:
            return "skipped"

        try:
            facts = self._to_facts(row, names.get(row.major_id))
            text = await self._ask(facts)
            await self.repository.set_llm_report(row.id, text)
            await self.repository.session.commit()

        except ReportDataIncomplete as exc:
            log.warning("집계 누락 report_id=%s %s", row.id, exc.detail)
            return "blocked"

        except Exception:
            log.exception("리포트 생성 실패 report_id=%s", row.id)
            await self.repository.session.rollback()
            return "retryable"

        return "generated"


    @staticmethod
    def _to_facts(
        row: Report,
        major_name: Optional[str]
    ) -> ReportFacts:
        missing = [
            name
            for name, value in (
                ("popular_tech_stacks", row.popular_tech_stacks),
                ("tech_mentions", row.tech_mentions),
            )
            if not value
        ]
        if missing:
            raise ReportDataIncomplete(row.id, missing)

        return ReportFacts(
            report_id=row.id,
            week_start_date=row.week_start_date,
            major_name=major_name,
            posting_count=row.weekly_collected_posting_count,
            popular=[
                PopularTechStack.model_validate(x)
                for x in row.popular_tech_stacks
            ],
            mentions=[
                TechMention.model_validate(x)
                for x in row.tech_mentions
            ],
            max_increase=(
                TechChange.model_validate(row.max_increase_tech)
                if row.max_increase_tech else None
            ),
            max_decrease=(
                TechChange.model_validate(row.max_decrease_tech)
                if row.max_decrease_tech else None
            ),
        )


    async def _ask(self, facts: ReportFacts) -> str:
        response = await self.model.ainvoke([
            SystemMessage(content=prompt.SYSTEM),
            HumanMessage(content=prompt.build(facts)),
        ])

        text = (response.content or "").strip()

        if len(text) < settings.report_min_length:
            raise ReportGenerationFailed(
                facts.report_id,
                f"응답 {len(text)}자"
            )

        return text