from functools import cache

from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.domains.chat.graph.nodes import call_model
from app.domains.chat.tools.context import ToolContext
from app.domains.chat.tools.registry import chat_tools


@cache
def build_graph() -> CompiledStateGraph:
    builder = StateGraph(MessagesState, context_schema=ToolContext)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode(chat_tools()))

    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model",
        tools_condition,
        {"tools": "tools", END: END}
    )
    builder.add_edge("tools", "model")

    return builder.compile()