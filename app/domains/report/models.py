# 주간 LLM 리포트. 백엔드 db 모델 확전 전까지 이거 쓴다.

from datetime import UTC, date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel, DateTime, Text


class WeeklyReport(SQLModel, table=True):
    __tablename__ = "weekly_report"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(default=None, primary_key=True)
    llm_report: str = Field(sa_type=Text)