# 리포트 테이블

from typing import Optional

from sqlmodel import SQLModel, Field

from datetime import datetime


class Report(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    llm_report: str = Field(default=None)
