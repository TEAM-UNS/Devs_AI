from datetime import date
from typing import Optional

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.report.models import Report


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def get(self, report_id: int) -> Optional[Report]:
        return (
            await self.session.exec(
                select(Report).where(Report.id == report_id)
            )
        ).first()


    async def get_by_week(
        self,
        week_start_date: date,
        major_id: Optional[int]
    ) -> Optional[Report]:
        condition = (
            Report.major_id.is_(None)
            if major_id is None
            else Report.major_id == major_id
        )

        return (
            await self.session.exec(
                select(Report)
                .where(
                    Report.week_start_date == week_start_date,
                    condition
                )
                .order_by(Report.created_at.desc())
            )
        ).first()


    async def set_llm_report(self, report_id: int, text: str) -> int:
        result = await self.session.exec(
            update(Report)
            .where(Report.id == report_id)
            .values(llm_report=text)
        )

        return result.rowcount or 0