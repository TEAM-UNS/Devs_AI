from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from app.domains.chat import service
from app.domains.chat.schemas import StreamRequest

chat_router = APIRouter(prefix="/api/chat", tags=["chat"])


@chat_router.post("/stream")
async def stream(body: StreamRequest) -> EventSourceResponse:
    return EventSourceResponse(
        service.stream(body.message),
        headers={"X-Accel-Buffering": "no"},
    )