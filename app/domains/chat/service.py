import logging

import json

import asyncio
from collections.abc import AsyncIterator

from sse_starlette import ServerSentEvent

from langchain_core.messages import HumanMessage

from app.core.database import session_factory
from app.domains.chat.enums import StreamEvent
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


async def stream(message: str) -> AsyncIterator[ServerSentEvent]:
    answer: list[str] = []
    tools_used: list[str] = []
    graph_ids: list[str] = []

    try:
        async for mode, chunk in build_graph().astream(
            {"messages": [HumanMessage(message)]},
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

    logger.info("chat stream 완료 — 툴 %s", tools_used or "없음")

    yield _event(
        StreamEvent.DONE,
        {
            "tools_used": tools_used,
            "graph_ids": graph_ids
         })