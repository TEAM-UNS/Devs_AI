from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.chat.queries import ChatQueries


@dataclass
class ToolContext:
    session_factory: async_sessionmaker[AsyncSession]

    @asynccontextmanager
    async def queries(self) -> AsyncIterator[ChatQueries]:
        async with self.session_factory() as session:
            yield ChatQueries(session)