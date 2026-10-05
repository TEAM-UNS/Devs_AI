from typing import Any

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.domains.chat.tools.charts import bar_chart, table_chart
from app.domains.chat.tools.context import ToolContext


@tool(
    "get_company_profile",
    description=(
        "기업 한 곳의 공고 수·요구 기술·경력 분포를 돌려준다. 이름이 애매하면 후보 목록을 준다. "
        "'네이버는 뭘 요구해' 같은 질문에 쓴다."
    ),
)
async def get_company_profile(name: str, top: int = 10) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "get_company_profile",
            "label": f"{name} 공고를 보고 있어요",
            "arguments": {"name": name, "top": top},
        }
    )

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.company_profile(name, top=top)

    if result.status != "found":
        return {
            "status": result.status,
            "name": name,
            "candidates": [
                {
                    "company_id": c.company_id,
                    "name": c.name,
                    "postings": c.posting_count
                }
                for c in result.candidates
            ],
        }

    profile = result.profile
    writer(
        {
            "type": "graph",
            "tool": "get_company_profile",
            "chart": bar_chart(
                title=f"{profile.name} 가 요구하는 기술",
                unit="공고 수",
                rows=[
                    (item.skill, item.posting_count)
                    for item in profile.top_skills
                ],
            ),
        }
    )

    return {
        "status": "found",
        "company_id": profile.company_id,
        "name": profile.name,
        "size_type": profile.size_type,
        "employee_count": profile.employee_count,
        "industry": profile.industry,
        "homepage": profile.homepage,
        "description": profile.description,
        "posting_count": profile.posting_count,
        "skills": [
            {
                "rank": item.rank,
                "name": item.skill,
                "postings": item.posting_count
            }
            for item in profile.top_skills
        ],
        "career_distribution": profile.career_distribution,
        "locations": profile.locations,
    }


@tool(
    "compare_companies",
    description=(
        "기업 두 곳 이상의 요구 기술을 비교한다. 회사 이름을 그대로 넘기면 된다. "
        "'네이버랑 카카오 스택 차이' 같은 질문에 쓴다."
    ),
)
async def compare_companies(names: list[str], top: int = 10) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "compare_companies",
            "label": "기업별 요구 기술을 맞춰보고 있어요",
            "arguments": {"names": names, "top": top},
        }
    )

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        company_ids: list[int] = []
        unresolved: list[str] = []
        for name in names:
            lookup = await queries.company_profile(name, top=1)
            if lookup.status == "found":
                company_ids.append(lookup.profile.company_id)
            else:
                unresolved.append(name)

        if len(company_ids) < 2:
            return {
                "found": False,
                "unresolved": unresolved
            }

        result = await queries.compare_companies(company_ids, top=top)

    writer(
        {
            "type": "graph",
            "tool": "compare_companies",
            "chart": table_chart(
                title="기업별 요구 기술",
                columns=["기업", "기술", "공고 수"],
                rows=[
                    [
                        company.name,
                        skill.skill,
                        skill.posting_count
                    ]
                    for company in result.companies
                    for skill in company.top_skills
                ],
            ),
        }
    )

    return {
        "found": True,
        "unresolved": unresolved,
        "shared_skills": result.shared_skills,
        "companies": [
            {
                "name": company.name,
                "size_type": company.size_type,
                "postings": company.posting_count,
                "skills": [
                    {
                        "name": s.skill,
                        "postings": s.posting_count
                    }
                    for s in company.top_skills
                ],
            }
            for company in result.companies
        ],
    }


@tool(
    "get_similar_companies",
    description=(
        "기술 스택과 사업 내용이 비슷한 기업을 찾는다. 기준 기업 이름을 넘긴다. "
        "'네이버랑 비슷한 회사' 같은 질문에 쓴다."
    ),
)
async def get_similar_companies(name: str, top: int = 5) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "get_similar_companies",
            "label": f"{name} 와 비슷한 기업을 찾고 있어요",
            "arguments": {
                "name": name,
                "top": top
            },
        }
    )

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        lookup = await queries.company_profile(name, top=1)
        if lookup.status != "found":
            return {
                "found": False,
                "name": name,
                "candidates": [c.name for c in lookup.candidates],
            }

        result = await queries.similar_companies(lookup.profile.company_id, top=top)

    if result is None:
        return {
            "found": False,
            "name": lookup.profile.name,
            "reason": "비교할 정보가 부족하다"
        }

    return {
        "found": True,
        "base": result.name,
        "companies": [
            {
                "rank": item.rank,
                "name": item.name,
                "size_type": item.size_type,
                "score": item.score,
                "shared_skills": item.shared_skills,
            }
            for item in result.items
        ],
    }