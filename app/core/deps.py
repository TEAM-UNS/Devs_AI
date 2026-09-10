from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.redis import get_redis

SessionDep = Annotated[AsyncSession, Depends(get_session)]

RedisDep = Annotated[ArqRedis, Depends(get_redis)]
