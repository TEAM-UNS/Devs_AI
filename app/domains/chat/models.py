from datetime import datetime
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import (
    Field, 
    SQLModel, 
    BigInteger, 
    DateTime, 
    String, 
    Text, 
    Column, 
    func, 
    UniqueConstraint
)
from app.domains.chat import enums


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_session"
    __table_args__ = {"schema": "chat"}

    id: Optional[int] = Field(
        default=None,
        primary_key=True
    )
    user_id: int = Field(
        sa_type=BigInteger,
        index=True,
        foreign_key="public.tbl_user.user_id",
        ondelete="CASCADE",
    )

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

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

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



# ── 유저 ─────
class PersonalHistory(enums.StrEnum):
    NO_EXPERIENCE = "NO_EXPERIENCE"
    ENTRY_LEVEL = "ENTRY_LEVEL"
    JUNIOR = "JUNIOR"
    MIDDLE = "MIDDLE"
    SENIOR = "SENIOR"


class User(SQLModel, table=True):
    __tablename__ = "tbl_user"
    __table_args__ = {"schema": "public"}

    user_id: int = Field(primary_key=True, sa_type=BigInteger)
    email: str = Field(max_length=255, unique=True)
    name: str = Field(max_length=255)
    password: str = Field(max_length=255)
    personal_history: PersonalHistory = Field(sa_type=String(255))


class UserSkill(SQLModel, table=True):
    __tablename__ = "tbl_user_skill"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_id", name="uk_user_skill"),
        {"schema": "public"},
    )

    user_skill_id: int = Field(primary_key=True, sa_type=BigInteger)
    user_id: int = Field(sa_type=BigInteger, foreign_key="public.tbl_user.user_id")
    skill_id: int = Field(foreign_key="market.skill.id")


class UserMajor(SQLModel, table=True):
    __tablename__ = "tbl_user_major"
    __table_args__ = (
        UniqueConstraint("user_id", "field_id", name="uk_user_major"),
        {"schema": "public"},
    )

    user_major_id: int = Field(primary_key=True, sa_type=BigInteger)
    user_id: int = Field(sa_type=BigInteger, foreign_key="public.tbl_user.user_id")
    field_id: int = Field(foreign_key="market.tech_field.id")