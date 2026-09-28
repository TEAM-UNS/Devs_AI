from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from app.domains.chat import service
from app.domains.chat.schemas import StreamRequest


chat_router = APIRouter(prefix="/api/chat", tags=["chat"])


@chat_router.post("/stream")
async def stream(body: StreamRequest) -> EventSourceResponse:
    session_id, is_new, history = await service.open_session(body)

    return EventSourceResponse(
        service.stream(
            session_id,
            body.message,
            is_new,
            history,
        ),
        headers={"X-Accel-Buffering": "no"},
    )