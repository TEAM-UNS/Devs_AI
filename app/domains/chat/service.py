import logging

import json
import asyncio
from typing import Optional

from collections.abc import AsyncIterator

from sse_starlette import ServerSentEvent

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
)

from app.core.database import session_factory
from app.domains.chat.exceptions import SessionNotFound
from app.domains.chat.enums import (
    StreamEvent,
    MessageRole,
)
from app.domains.chat.repository import ChatRepository
from app.domains.chat.models import ChatMessage
from app.domains.chat.schemas import StreamRequest
from app.domains.chat.graph.build import build_graph
from app.domains.chat.tools.context import ToolContext


logger = logging.getLogger(__name__)


def _event(name: StreamEvent, payload: dict) -> ServerSentEvent:
    return ServerSentEvent(
        event=name.value,
        data=json.dumps(
            payload,
            ensure_ascii=False
        )
    )

# 도구 호출 기록 일단 안 넣음. 나중에 비교 ㄱㄱ
def _to_messages(rows: list[ChatMessage]) -> list[BaseMessage]:
    messages: list[BaseMessage] = []

    for row in rows:
        if row.role == MessageRole.USER:
            messages.append(HumanMessage(row.content))
        elif row.role == MessageRole.ASSISTANT:
            messages.append(AIMessage(row.content))

    return messages


async def open_session(request: StreamRequest) -> tuple[int, bool, list[BaseMessage]]:
    is_new = request.session_id is None
    history: list[BaseMessage] = []

    async with session_factory() as db:
        repository = ChatRepository(db)

        if is_new:
            chat_session = await repository.create_session(request.user_id)
        else:
            chat_session = await repository.get_session(
                request.session_id,
                request.user_id
            )
            if chat_session is None:
                raise SessionNotFound(request.session_id)

            row = await repository.recent_messages(request.session_id)
            history = _to_messages(row)

        await repository.add_message(
            chat_session.id,
            MessageRole.USER,
            request.message
        )

        await db.commit()

    return chat_session.id, is_new, history


async def _save_answer(
    session_id: int,
    answer: list[str]
) -> Optional[int]:
    text = "".join(answer).strip()
    if not text:
        return None

    async with session_factory() as db:
        message = await ChatRepository(db).add_message(
            session_id,
            MessageRole.ASSISTANT,
            text
        )
        await db.commit()
        return message.id


async def stream(
    session_id,
    message: str,
    is_new: bool,
    history: list[BaseMessage],
) -> AsyncIterator[ServerSentEvent]:
    answer: list[str] = []
    tools_used: list[str] = []
    graph_ids: list[str] = []

    yield _event(
        StreamEvent.SESSION,
        {"session_id": session_id, "is_new": is_new}
    )

    try:
        async for mode, chunk in build_graph().astream(
            {"messages": [*history, HumanMessage(message)]},
            context=ToolContext(session_factory=session_factory),
            stream_mode=["messages", "custom"],
        ):
            if mode == "custom":
                if chunk["type"] == "tool_start":
                    tools_used.append(chunk["tool"])
                    yield _event(
                        StreamEvent.TOOL_START,
                        {
                            "tool": chunk["tool"],
                            "label": chunk["label"],
                        })

                elif chunk["type"] == "graph":
                    chart_id = f"g_{len(graph_ids) + 1:02d}"
                    graph_ids.append(chart_id)
                    yield _event(
                        StreamEvent.GRAPH,
                        {
                            "id": chart_id,
                            **chunk["chart"]
                        })
                continue

            message_chunk, meta = chunk

            if meta.get("langgraph_node") != "model" or not message_chunk.text:
                continue

            answer.append(message_chunk.text)
            yield _event(
                StreamEvent.TOKEN,
                {"text": message_chunk.text}
            )

    except asyncio.CancelledError:
        logger.info(
            "chat stream 중단 — 연결 끊김 (%d자 생성)",
            len("".join(answer))
        )
        await asyncio.shield(_save_answer(session_id, answer))
        raise

    except Exception:
        logger.exception("chat stream 실패")

        yield _event(
            StreamEvent.ERROR,
            {
            "code": "INTERNAL_ERROR",
            "message": "답변 생성에 실패했습니다.",
            "recoverable": False,
        })
        return

    message_id = await _save_answer(session_id, answer)

    logger.info(
        "chat stream 완료 — 툴 %s",
        tools_used or "없음"
    )

    yield _event(
        StreamEvent.DONE,
        {
            "message_id": message_id,
            "tools_used": tools_used,
            "graph_ids": graph_ids
         })