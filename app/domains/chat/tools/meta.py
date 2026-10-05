from typing import Any

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.domains.chat.tools.context import ToolContext


@tool(
    "get_data_coverage",
    description=(
        "우리가 가진 데이터의 범위를 돌려준다. 수집 기간·공고 수·직군 분포다. "
        "'데이터가 얼마나 있냐', '믿을 만하냐' 같은 질문이나 답변의 신뢰도를 밝힐 때 쓴다."
    ),
)
async def get_data_coverage() -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "get_data_coverage",
            "label": "데이터 범위를 확인하고 있어요",
            "arguments": {},
        }
    )

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.data_coverage()

    return {
        "posted_from": str(result.posted_from),
        "posted_to": str(result.posted_to),
        "total_postings": result.total_postings,
        "active_postings": result.active_postings,
        "company_count": result.company_count,
        "by_field": result.by_field,
        "image_only_ratio": result.image_only_ratio,
        "salary_disclosure_rate": result.salary_disclosure_rate,
        "unclassified_postings": result.unclassified_postings,
        "last_crawl_at": str(result.last_crawl_at) if result.last_crawl_at else None,
    }