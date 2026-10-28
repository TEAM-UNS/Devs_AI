from datetime import datetime
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import (
    Field,
    SQLModel,
    BigInteger,
    DateTime,
    SmallInteger,
    String,
    Text,
    Column,
    func
)
from app.domains.roadmap.enums import RoadmapStatus


class Roadmap(SQLModel, table=True):
    __tablename__ = "roadmap"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger
    )
    user_id: int = Field(
        sa_type=BigInteger,
        index=True,
        foreign_key="public.tbl_user.user_id",
        ondelete="CASCADE",
    )
    field_id: Optional[int] = Field(
        default=None,
        foreign_key="market.tech_field.id"
    )

    target_weeks: int = Field(sa_type=SmallInteger)

    skill_ids: list[int] = Field(
        default_factory=list,
        sa_type=JSONB
    )
    outline: Optional[dict[str, Any]] = Field(
        default=None,
        sa_type=JSONB
    )
    status: RoadmapStatus = Field(
        default=RoadmapStatus.PENDING,
        sa_type=String(20)
    )
    generated_weeks: int = Field(
        default=0,
        sa_type=SmallInteger
    )
    error_message: Optional[str] = Field(
        default=None,
        sa_type=Text
    )

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), 
            server_default=func.now(), 
            nullable=False
        )
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )


class RoadmapItem(SQLModel, table=True):
    __tablename__ = "roadmap_item"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger
    )
    roadmap_id: int = Field(
        sa_type=BigInteger,
        index=True,
        foreign_key="market.roadmap.id",
        ondelete="CASCADE",
    )

    week_no: int = Field(sa_type=SmallInteger)
    day_no: int = Field(sa_type=SmallInteger)
    seq: int = Field(sa_type=SmallInteger)

    title: str = Field(max_length=200)
    description: Optional[str] = Field(
        default=None,
        sa_type=Text
    )

    is_done: bool = Field(default=False)

    done_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True))
    )

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )