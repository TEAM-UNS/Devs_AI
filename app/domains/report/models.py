from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import (
    Field,
    SQLModel,
    BigInteger,
    Date,
    DateTime,
    Text,
    Column,
    func
)


class Report(SQLModel, table=True):
    __tablename__ = "report"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger
    )
    week_start_date: date = Field(sa_type=Date)

    major_id: Optional[int] = Field(
        default=None,
        foreign_key="market.tech_field.id"
    )

    popular_tech_stacks: Optional[list[dict[str, Any]]] = Field(
        default=None,
        sa_type=JSONB
    )
    max_increase_tech: Optional[dict[str, Any]] = Field(
        default=None,
        sa_type=JSONB
    )
    max_decrease_tech: Optional[dict[str, Any]] = Field(
        default=None,
        sa_type=JSONB
    )
    tech_mentions: Optional[list[dict[str, Any]]] = Field(
        default=None,
        sa_type=JSONB
    )
    weekly_collected_posting_count: Optional[int] = Field(
        default=None,
        sa_type=BigInteger
    )
    earliest_posting_date: Optional[date] = Field(
        default=None,
        sa_type=Date
    )

    llm_report: str = Field(sa_type=Text)

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )