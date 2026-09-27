from datetime import datetime
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import (
    Field, SQLModel, BigInteger, DateTime, String, Text, Column, func
)
from app.domains.chat import enums


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_session"
    __table_args__ = {"schema": "chat"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True
    )
    user_id: int = Field(index=True)

    title: Optional[str] = Field(
        default=None,
        max_length=200
    )
    last_message_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"
    __table_args__ = {"schema": "chat"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger
    )
    session_id: int = Field(
        foreign_key="chat.chat_session.id",
        ondelete="CASCADE",
        index=True
    )
    role: enums.MessageRole = Field(sa_type=String(16))

    content: str = Field(sa_type=Text)

    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False
        )
    )


class ChatToolCall(SQLModel, table=True):
    __tablename__ = "chat_tool_call"
    __table_args__ = {"schema": "chat"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger
    )
    message_id: int = Field(
        sa_type=BigInteger,
        foreign_key="chat.chat_message.id",
        ondelete="CASCADE",
        index=True
    )
    tool_name: str = Field(max_length=64)

    arguments: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSONB
    )
    chart_payload: Optional[dict[str, Any]] = Field(
        default=None,
        sa_type=JSONB
    )