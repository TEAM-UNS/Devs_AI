from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from app.core.dependencies import RedisDep
from app.domains.chat import service, guard
from app.domains.chat.schemas import StreamRequest


chat_router = APIRouter(prefix="/api/chat", tags=["chat"])


@chat_router.post("/stream")
async def stream(body: StreamRequest, redis: RedisDep) -> EventSourceResponse:
    headers = await guard.check(redis, body.user_id)
    session_id, is_new, history, profile = await service.open_session(body)

    return EventSourceResponse(
        service.stream(
            session_id,
            body.message,
            is_new,
            history,
            body.user_id,
            profile,
        ),
        headers={
            "X-Accel-Buffering": "no",
            **headers
        },
    )