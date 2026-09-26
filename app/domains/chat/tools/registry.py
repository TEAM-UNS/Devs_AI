from langchain_core.tools import BaseTool

from app.domains.chat.tools.trend import get_popular_skills


def chat_tools() -> list[BaseTool]:
    return [
        get_popular_skills,
    ]