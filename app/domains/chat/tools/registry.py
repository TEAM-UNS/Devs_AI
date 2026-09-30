from langchain_core.tools import BaseTool

from app.domains.chat.tools.trend import (
    get_popular_skills,
    get_rising_skills,
    get_salary_stats,
    compare_segments,
)
from app.domains.chat.tools.skill import (
    get_related_skills,
    get_skill_demand,
)


def chat_tools() -> list[BaseTool]:
    return [
        get_popular_skills,
        get_rising_skills,
        get_salary_stats,
        compare_segments,
        get_related_skills,
        get_skill_demand,
    ]