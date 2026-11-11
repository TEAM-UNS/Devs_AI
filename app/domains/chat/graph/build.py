from functools import cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.domains.chat.graph.nodes import ChatState, call_llm, call_jev, jev_routed
from app.domains.chat.tools.context import ToolContext
from app.domains.chat.tools.registry import chat_tools


@cache
def build_graph() -> CompiledStateGraph:
    builder = StateGraph(ChatState, context_schema=ToolContext)

    builder.add_node("jev", call_jev)
    builder.add_node("llm", call_llm)
    builder.add_node("tools", ToolNode(chat_tools()))

    builder.add_edge(START, "jev")
    builder.add_conditional_edges(
        "jev",
        jev_routed,
        {"tools": "tools", "llm": "llm"}
    )
    builder.add_conditional_edges(
        "llm",
        tools_condition,
        {"tools": "tools", END: END}
    )
    builder.add_edge("tools", "llm")

    return builder.compile()