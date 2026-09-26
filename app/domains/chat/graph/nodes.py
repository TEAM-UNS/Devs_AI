from functools import cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import MessagesState

from app.domains.chat.graph.prompts import system_prompt
from app.domains.chat.tools.registry import chat_tools
from app.infra.llm.client import build_chat_model


@cache
def _model() -> BaseChatModel:
    return build_chat_model().bind_tools(chat_tools())


async def call_model(state: MessagesState) -> dict[str, list[Any]]:
    response = await _model().ainvoke(
        [
            SystemMessage(system_prompt()),
            *state["messages"]
        ]
    )
    return {"messages": [response]}