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
    
    
class RoadmapItem(SQLModel, table=True):
    __tablename__ = "roadmap_item"
    __table_args__ = {"schema": "market"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger
    )