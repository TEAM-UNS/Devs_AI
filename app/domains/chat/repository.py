from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.chat.enums import MessageRole
from app.domains.chat.models import (
    ChatMessage,
    ChatSession,
    ChatToolCall,
    User,
    UserMajor,
    UserSkill
)
from app.domains.chat.schemas import UserProfile

from app.domains.crawler.enums import CareerLevel
from app.domains.crawler.models import Skill, TechField


class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def create_session(self, user_id: int) -> ChatSession:
        row = ChatSession(user_id=user_id)

        self.session.add(row)
        await self.session.flush()

        return row


    async def get_session(
        self,
        session_id: int,
        user_id: int
    ) -> Optional[ChatSession]:
        return (
            await self.session.exec(
                select(ChatSession).where(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                )
            )
        ).first()


    async def add_message(
        self, session_id: int,
        role: MessageRole,
        content: str,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> ChatMessage:
        row = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        self.session.add(row)

        await self.session.exec(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(last_message_at=datetime.now(UTC))
        )

        await self.session.flush()

        return row


    async def add_tool_calls(self, rows: list[ChatToolCall]):
        self.session.add_all(rows)
        await self.session.flush()


    async def recent_messages(
        self,
        session_id: int,
        limit: int = 12
    ) -> list[ChatMessage]:
        rows = await self.session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(rows.all()))


    async def update_title(
        self,
        session_id: int,
        title: str
    ):
        await self.session.exec(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(title=title)
        )

    @staticmethod
    def _career_level(personal_history: str) -> Optional[CareerLevel]:
        return {
            "NO_EXPERIENCE": CareerLevel.NEWCOMER,
            "ENTRY_LEVEL": CareerLevel.NEWCOMER,
            "JUNIOR": CareerLevel.JUNIOR,
            "MIDDLE": CareerLevel.MID,
            "SENIOR": CareerLevel.SENIOR,
        }.get(personal_history)


    async def get_profile(self, user_id: int) -> Optional[UserProfile]:
        user = (
            await self.session.exec(select(User).where(User.user_id == user_id))
        ).first()
        if user is None:
            return None

        fields = (
            await self.session.exec(
                select(TechField.code)
                .join(UserMajor, UserMajor.field_id == TechField.id)
                .where(UserMajor.user_id == user_id)
                .order_by(TechField.sort_order)
            )
        ).all()

        skills = (
            await self.session.exec(
                select(Skill.name)
                .join(UserSkill, UserSkill.skill_id == Skill.id)
                .where(UserSkill.user_id == user_id)
                .order_by(Skill.name)
            )
        ).all()

        return UserProfile(
            user_id=user_id,
            name=user.name,
            career_level=self._career_level(user.personal_history),
            fields=list(fields),
            skills=list(skills),
        )