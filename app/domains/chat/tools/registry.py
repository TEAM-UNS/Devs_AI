from langchain_core.tools import BaseTool

from app.domains.chat.tools.company import (
    compare_companies,
    get_company_profile,
    get_similar_companies,
)
from app.domains.chat.tools.meta import get_data_coverage
from app.domains.chat.tools.personal import analyze_skill_gap
from app.domains.chat.tools.search import (
    search_companies,
    search_postings,
)
from app.domains.chat.tools.skill import (
    get_related_skills,
    get_skill_demand,
)
from app.domains.chat.tools.trend import (
    compare_segments,
    get_popular_skills,
    get_rising_skills,
    get_salary_stats,
)


def chat_tools() -> list[BaseTool]:
    return [
        get_popular_skills,
        get_rising_skills,
        get_salary_stats,
        compare_segments,
        get_related_skills,
        get_skill_demand,
        get_company_profile,
        compare_companies,
        get_similar_companies,
        search_postings,
        search_companies,
        analyze_skill_gap,
        get_data_coverage,
    ]