from typing import Optional, Any

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.domains.chat.tools.charts import bar_chart
from app.domains.chat.tools.context import ToolContext
from app.domains.crawler.enums import TechField


@tool(
    "get_popular_skills",
    description=(
        "채용공고에서 많이 요구하는 기술을 공고 수 순으로 돌려준다. "
        "field 를 주면 그 직군만, 없으면 전체를 본다. days 는 최근 며칠을 볼지다."
    ),
)
async def get_popular_skills(
    field: Optional[TechField] = None,
    days: int = 30,
    top: int = 10
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({
        "type": "tool_start",
        "tool": "get_popular_skills",
        "label": "기술 수요를 집계하고 있어요",
    })

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.popular_skills(
            field=field, days=days, top=top
        )

    writer({
        "type": "graph",
        "chart": bar_chart(
            title=f"{field.value if field else '전체'} 직군에서 많이 요구되는 기술",
            unit="공고 수",
            rows=[
                (item.skill, item.posting_count)
                for item in result.items
            ],
        ),
    })

    return {
        "analyzed_postings": result.analyzed_postings,
        "total_postings": result.total_postings,
        "days": result.days,
        "skills": [
            {
                "name": item.skill,
                "postings": item.posting_count,
                "share": item.share
            }
            for item in result.items
        ],
    }