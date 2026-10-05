from typing import Any, Optional

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.domains.chat.tools.charts import bar_chart
from app.domains.chat.tools.context import ToolContext
from app.domains.crawler.enums import CareerLevel, TechField


@tool(
    "analyze_skill_gap",
    description=(
        "사용자가 가진 기술과 채용공고가 요구하는 기술을 비교해 부족한 것을 찾는다. "
        "skills 를 비우면 사용자 프로필의 보유 스킬을 쓴다. "
        "'내가 뭘 더 배워야 해' 같은 질문에 쓴다."
    ),
)
async def analyze_skill_gap(
    skills: Optional[list[str]] = None,
    field: Optional[TechField] = None,
    career_level: Optional[CareerLevel] = None,
    top: int = 15,
) -> dict[str, Any]:
    runtime = get_runtime(ToolContext)
    profile = runtime.context.profile

    my_skills = skills or (profile.skills if profile else [])
    if field is None and profile and profile.fields:
        field = TechField(profile.fields[0])
    if career_level is None and profile:
        career_level = profile.career_level

    if not my_skills:
        return {"found": False, "reason": "보유 기술을 알 수 없다"}

    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "analyze_skill_gap",
            "label": "보유 기술과 공고 요구사항을 맞춰보고 있어요",
            "arguments": {
                "skills": my_skills,
                "field": field.value if field else None,
                "career_level": career_level.value if career_level else None,
                "top": top,
            },
        }
    )

    async with runtime.context.queries() as queries:
        result = await queries.skill_gap(
            my_skills,
            field=field,
            career_level=career_level,
            top=top
        )

    writer(
        {
            "type": "graph",
            "tool": "analyze_skill_gap",
            "chart": bar_chart(
                title="아직 없는 기술 (공고 요구 순)",
                unit="공고 수",
                rows=[
                    (item.skill, item.posting_count)
                    for item in result.missing
                ],
            ),
        }
    )

    return {
        "found": True,
        "matched": result.matched,
        "unknown_inputs": result.unknown_inputs,
        "analyzed_postings": result.analyzed_postings,
        "coverage": result.coverage,
        "missing": [
            {
                "rank": item.rank,
                "name": item.skill,
                "postings": item.posting_count,
                "share": item.share,
            }
            for item in result.missing
        ],
    }