from datetime import date

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.crawler.models import TechField
from app.domains.report.models import Report


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def list_by_week(self, week_start_date: date) -> list[Report]:
        rows = await self.session.exec(
            select(Report)
            .where(Report.week_start_date == week_start_date)
            .order_by(Report.major_id)
        )

        return list(rows.all())


    async def weeks_with_missing(self, since: date) -> list[date]:
        rows = await self.session.exec(
            select(Report.week_start_date)
            .where(
                Report.week_start_date >= since,
                Report.llm_report.is_(None),
            )
            .distinct()
            .order_by(Report.week_start_date)
        )

        return list(rows.all())


    async def field_names(self) -> dict[int, str]:
        rows = await self.session.exec(
            select(TechField.id, TechField.code)
        )

        return {
            row[0]: row[1]
            for row in rows.all()
        }


    async def set_llm_report(self, report_id: int, text: str):
        await self.session.exec(
            update(Report)
            .where(Report.id == report_id)
            .values(llm_report=text)
        )