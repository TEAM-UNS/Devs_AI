from functools import cache
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState
from langgraph.runtime import get_runtime

from app.domains.chat.graph.decision import decide
from app.domains.chat.graph.prompts import (
    system_prompt,
    profile_prompt,
)
from app.domains.chat.tools.registry import chat_tools
from app.domains.chat.tools.context import ToolContext
from app.infra.llm.client import build_chat_model
from app.infra.llm.tracing import node_trace_context


class ChatState(MessagesState):
    narrow_tool: Optional[str]


@cache
def _model() -> BaseChatModel:
    return build_chat_model().bind_tools(chat_tools())


@cache
def _narrow_model(name: str) -> BaseChatModel:
    tool = next(t for t in chat_tools() if t.name == name)
    return build_chat_model().bind_tools([tool], tool_choice=name)


async def call_jev(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    return await decide(
        state["messages"],
        get_runtime(ToolContext).context.profile,
        node_trace_context(config),
    )


def jev_routed(state: ChatState) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else "gemini"


async def call_gemini(state: ChatState) -> dict[str, Any]:
    messages = [SystemMessage(system_prompt())]

    profile = get_runtime(ToolContext).context.profile
    if profile is not None:
        messages.append(
            SystemMessage(profile_prompt(profile))
        )

    narrow = state.get("narrow_tool")
    model = _narrow_model(narrow) if narrow else _model()
    response = await model.ainvoke(
        [
            *messages,
            *state["messages"]
        ]
    )
    return {"messages": [response], "narrow_tool": None}