from functools import cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import MessagesState
from langgraph.runtime import get_runtime

from app.domains.chat.graph.prompts import (
    system_prompt,
    profile_prompt,
)
from app.domains.chat.tools.registry import chat_tools
from app.domains.chat.tools.context import ToolContext
from app.infra.llm.client import build_chat_model


@cache
def _model() -> BaseChatModel:
    return build_chat_model().bind_tools(chat_tools())


async def call_model(state: MessagesState) -> dict[str, list[Any]]:
    messages = [SystemMessage(system_prompt())]

    profile = get_runtime(ToolContext).context.profile
    if profile is not None:
        messages.append(
            SystemMessage(profile_prompt(profile))
        )

    response = await _model().ainvoke(
        [
            *messages,
            *state["messages"]
        ]
    )
    return {"messages": [response]}