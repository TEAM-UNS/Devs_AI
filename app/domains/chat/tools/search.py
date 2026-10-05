from typing import Any, Optional

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.domains.chat.tools.context import ToolContext
from app.domains.crawler.enums import CompanySize, TechField


@tool(
    "search_postings",
    description=(
        "질문 문장과 의미가 비슷한 공고를 찾는다. 기술 이름이 아니라 "
        "'지도 서비스 만드는 곳' 처럼 설명으로 찾을 때 쓴다."
    ),
)
async def search_postings(
    query: str,
    field: Optional[TechField] = None,
    top: int = 10,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "search_postings",
            "label": "비슷한 공고를 찾고 있어요",
            "arguments": {
                "query": query,
                "field": field.value if field else None, "top": top
            },
        }
    )

    runtime = get_runtime(ToolContext)
    query_vec = await runtime.context.embedder.embed_query(query)

    async with runtime.context.queries() as queries:
        hits = await queries.search_postings(
            query_vec=query_vec,
            field=field,
            top=top
        )

    return {
        "query": query,
        "postings": [
            {
                "posting_id": hit.posting_id,
                "title": hit.title,
                "company": hit.company,
                "url": hit.url,
                "section": hit.section,
                "excerpt": hit.chunk[:200],
                "similarity": round(hit.similarity, 4),
            }
            for hit in hits
        ],
    }


@tool(
    "search_companies",
    description=(
        "사업 설명이 질문과 비슷한 기업을 찾는다. '핀테크 하는 스타트업' 처럼 "
        "업종·분야로 찾을 때 쓴다."
    ),
)
async def search_companies(
    query: str,
    size_type: Optional[CompanySize] = None,
    field: Optional[TechField] = None,
    top: int = 10,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "search_companies",
            "label": "조건에 맞는 기업을 찾고 있어요",
            "arguments": {
                "query": query,
                "size_type": size_type.value if size_type else None,
                "field": field.value if field else None,
                "top": top,
            },
        }
    )

    runtime = get_runtime(ToolContext)
    query_vec = await runtime.context.embedder.embed_query(query)

    async with runtime.context.queries() as queries:
        hits = await queries.search_companies(
            query_vec=query_vec,
            size_type=size_type,
            field=field,
            top=top
        )

    return {
        "query": query,
        "companies": [
            {
                "company_id": hit.company_id,
                "name": hit.name,
                "size_type": hit.size_type,
                "industry": hit.industry,
                "postings": hit.posting_count,
                "evidence": hit.evidence,
                "similarity": round(hit.similarity, 4),
            }
            for hit in hits
        ],
    }