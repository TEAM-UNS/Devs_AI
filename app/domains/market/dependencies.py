# market 쿼리 FastAPI 의존성

from typing import Annotated

from fastapi import Depends

from app.core.deps import SessionDep

from app.domains.market.queries import ChatQueries


def get_market_queries(session: SessionDep) -> ChatQueries:
    return ChatQueries(session)


MarketQueriesDep = Annotated[ChatQueries, Depends(get_market_queries)]