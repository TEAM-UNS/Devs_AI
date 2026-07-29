"""chat 스키마가 소유하는 테이블 (schema="chat").

    chat_session    id(uuid) · user_id(토큰 sub) · title · message_count
                    last_message_at · deleted_at(soft delete)
    chat_message    session_id · seq(세션 내 유일) · role · content · token_count
    chat_tool_call  message_id · tool_name · arguments · result
                    chart_payload(그래프 복원) · latency_ms · is_error

chat_message 는 "표시용" 이력이다. LLM 컨텍스트는 LangGraph checkpointer 가
따로 관리하며, checkpointer 테이블은 라이브러리가 chat 스키마에 직접 만든다
(여기서 정의하지 않고 alembic 관리 대상도 아니다).

★ `from __future__ import annotations` 를 쓰지 않는다. 어노테이션이 문자열이
  되면 SQLModel 이 Relationship 대상을 해석하지 못한다.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlmodel import Field, Relationship, SQLModel

from app.core import enums

SCHEMA = "chat"


def _created_at() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_session"
    __table_args__ = (
        # 목록 조회는 삭제되지 않은 세션만 본다
        Index(
            "chat_session_user_idx",
            "user_id",
            text("last_message_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        ),
    )
    user_id: str = Field(max_length=64)  # dev: X-User-Id / jwt: sub 클레임
    title: str | None = Field(default=None, max_length=200)  # 첫 질문으로 자동 생성
    message_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    last_message_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    created_at: datetime | None = Field(default=None, sa_column=_created_at())
    updated_at: datetime | None = Field(default=None, sa_column=_created_at())
    # soft delete. 조회 시 IS NULL 조건 필수.
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    messages: list["ChatMessage"] = Relationship(back_populates="session", cascade_delete=True)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"
    __table_args__ = (
        CheckConstraint(enums.sql_in("role", enums.MessageRole), name="chat_message_role_chk"),
        UniqueConstraint("session_id", "seq", name="chat_message_uk"),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    session_id: uuid.UUID = Field(
        sa_type=PgUUID(as_uuid=True),
        foreign_key=f"{SCHEMA}.chat_session.id",
        ondelete="CASCADE",
    )
    seq: int
    # Enum 은 String 으로 고정한다 (market/models.py 상단 주석 참고)
    role: enums.MessageRole = Field(sa_type=String(16))
    content: str = Field(default="", sa_type=Text, sa_column_kwargs={"server_default": text("''")})
    token_count: int | None = None
    created_at: datetime | None = Field(default=None, sa_column=_created_at())

    session: ChatSession | None = Relationship(back_populates="messages")
    tool_calls: list["ChatToolCall"] = Relationship(back_populates="message", cascade_delete=True)


class ChatToolCall(SQLModel, table=True):
    """툴 호출 로그.

    chart_payload 로 세션 재진입 시 LLM 재호출 없이 그래프를 복원한다.
    차트화 불가한 툴이면 None.
    """

    __tablename__ = "chat_tool_call"
    __table_args__ = (
        Index("chat_tool_call_message_idx", "message_id", "seq"),
        # 툴별 성능 · 실패율 확인용
        Index("chat_tool_call_tool_idx", "tool_name", text("created_at DESC")),
        {"schema": SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    message_id: int = Field(
        sa_type=BigInteger,
        foreign_key=f"{SCHEMA}.chat_message.id",
        ondelete="CASCADE",
    )
    seq: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})  # 한 턴에 여러 호출
    tool_name: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    chart_payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    latency_ms: int | None = None
    is_error: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    created_at: datetime | None = Field(default=None, sa_column=_created_at())

    message: ChatMessage | None = Relationship(back_populates="tool_calls")
