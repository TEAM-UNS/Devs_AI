from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.chat.enums import MessageRole
from app.domains.chat.models import (
    ChatMessage,
    ChatSession,
    ChatToolCall
)


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