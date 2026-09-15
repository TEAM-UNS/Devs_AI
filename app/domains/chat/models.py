from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Column, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

from app.domains.chat import enums


def _timestamp() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_session"
    __table_args__ = {"schema": "chat"}

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(max_length=64, index=True)
    title: Optional[str] = Field(default=None, max_length=200)
    message_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    last_message_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())
    updated_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())
    deleted_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))

    messages: list["ChatMessage"] = Relationship(back_populates="session", cascade_delete=True)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"
    __table_args__ = {"schema": "chat"}

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    session_id: int = Field(foreign_key="chat.chat_session.id", ondelete="CASCADE")
    seq: int
    role: enums.MessageRole = Field(sa_type=String(16))
    content: str = Field(default="", sa_type=Text, sa_column_kwargs={"server_default": text("''")})
    token_count: Optional[int] = None
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    session: Optional[ChatSession] = Relationship(back_populates="messages")
    tool_calls: list["ChatToolCall"] = Relationship(back_populates="message", cascade_delete=True)


class ChatToolCall(SQLModel, table=True):
    __tablename__ = "chat_tool_call"
    __table_args__ = {"schema": "chat"}

    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
    message_id: int = Field(
        sa_type=BigInteger, foreign_key="chat.chat_message.id", ondelete="CASCADE", index=True
    )
    seq: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    tool_name: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(
        default_factory=dict, sa_type=JSONB, sa_column_kwargs={"server_default": text("'{}'::jsonb")}
    )
    result: Optional[dict[str, Any]] = Field(default=None, sa_type=JSONB)
    chart_payload: Optional[dict[str, Any]] = Field(default=None, sa_type=JSONB)
    latency_ms: Optional[int] = None
    is_error: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    created_at: Optional[datetime] = Field(default=None, sa_column=_timestamp())

    message: Optional[ChatMessage] = Relationship(back_populates="tool_calls")
