from typing import Any, Optional

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.domains.chat.tools.charts import bar_chart
from app.domains.chat.tools.context import ToolContext
from app.domains.crawler.enums import Requirement, TechField


@tool(
    "get_skill_demand",
    description=(
        "특정 기술 하나가 어디서 얼마나 요구되는지 돌려준다. 직군·회사 규모·경력·요구 강도별로 나뉜다. "
        "기술 이름은 별칭(리액트, react)으로 줘도 사전이 찾아낸다."
    ),
)
async def get_skill_demand(skill: str) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "get_skill_demand",
            "label": f"{skill} 수요를 보고 있어요",
            "arguments": {"skill": skill},
        }
    )

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.skill_demand(skill)

    # 사전에 없는 기술이다. 모델이 지어내지 말고 되물어야 한다
    if result is None:
        return {"found": False, "skill": skill}

    writer(
        {
            "type": "graph",
            "tool": "get_skill_demand",
            "chart": bar_chart(
                title=f"{result.skill} 를 요구하는 공고 (직군별)",
                unit="공고 수",
                rows=list(result.by_field.items()),
            ),
        }
    )

    return {
        "found": True,
        "skill": result.skill,
        "total_postings": result.total_postings,
        "by_field": result.by_field,
        "by_size": result.by_size,
        "by_career": result.by_career,
        "by_requirement": result.by_requirement,
    }


@tool(
    "get_related_skills",
    description=(
        "어떤 기술과 같이 요구되는 기술을 돌려준다. 단순 동시 등장 수가 아니라 "
        "연관도(npmi)로 정렬한다. 'React 쓰는 회사는 뭘 같이 요구해' 같은 질문에 쓴다."
    ),
)
async def get_related_skills(
    skill: str,
    field: Optional[TechField] = None,
    requirement: Optional[Requirement] = None,
    top: int = 10,
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "get_related_skills",
            "label": f"{skill} 와 함께 요구되는 기술을 찾고 있어요",
            "arguments": {
                "skill": skill,
                "field": field.value if field else None,
                "requirement": requirement.value if requirement else None,
                "top": top,
            },
        }
    )

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.related_skills(skill, field=field, requirement=requirement, top=top)

    if result is None:
        return {"found": False, "skill": skill}

    writer(
        {
            "type": "graph",
            "tool": "get_related_skills",
            "chart": bar_chart(
                title=f"{result.skill} 와 함께 요구되는 기술",
                unit="동시 등장 공고 수",
                rows=[(item.skill, item.cooccurrence) for item in result.items],
            ),
        }
    )

    return {
        "found": True,
        "skill": result.skill,
        "base_postings": result.base_postings,
        "related": [
            {"name": item.skill, "cooccurrence": item.cooccurrence, "npmi": item.npmi}
            for item in result.items
        ],
    }
