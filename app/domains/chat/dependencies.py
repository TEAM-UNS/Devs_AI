# chat 의존성 조립

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import SessionDep
from app.domains.chat.queries import ChatQueries


def get_chat_queries(session: SessionDep) -> ChatQueries:
    return ChatQueries(session)


ChatQueriesDep = Annotated[ChatQueries, Depends(get_chat_queries)]
