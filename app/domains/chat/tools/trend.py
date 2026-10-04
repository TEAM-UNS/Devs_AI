from typing import Optional, Any, Literal

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.domains.chat.tools.charts import (
    bar_chart,
    grouped_bar_chart,
    table_chart
)
from app.domains.chat.tools.context import ToolContext
from app.domains.crawler.enums import (
    TechField,
    CareerLevel,
    CompanySize,
)


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
    arguments = {
        "field": field.value if field else None,
        "days": days,
        "top": top,
    }
    
    writer = get_stream_writer()
    writer(
        {
            "type": "tool_start",
            "tool": "get_popular_skills",
            "label": "기술 수요를 집계하고 있어요",
            "arguments": arguments,
        }
    )

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.popular_skills(
            field=field, days=days, top=top
        )

    writer(
        {
            "type": "graph",
            "tool": "get_popular_skills",
            "chart": bar_chart(
                title=f"{field.value if field else '전체'} 직군에서 많이 요구되는 기술",
                unit="공고 수",
                rows=[(item.skill, item.posting_count) for item in result.items],
            ),
        }
    )

    return {
        "analyzed_postings": result.analyzed_postings,
        "total_postings": result.total_postings,
        "days": result.days,
        "skills": [
            {
                "rank": item.rank,
                "name": item.skill,
                "postings": item.posting_count,
                "share": item.share
            }
            for item in result.items
        ],
    }


@tool(
    "get_rising_skills",
    description=(
        "최근 급상승·급하락한 기술을 돌려준다. window_days 기간을 직전 같은 기간과 비교한다. "
        "직전 기간에 없다가 새로 나타난 기술은 newcomers 로 따로 준다. "
        "'요즘 뜨는 기술' 같은 질문에 쓴다."
    ),
)
async def get_rising_skills(
    field: Optional[TechField] = None,
    window_days: int = 7,
    top: int = 10
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({
        "type": "tool_start",
        "tool": "get_rising_skills",
        "label": "기술 증감을 비교하고 있어요",
        "arguments": {
            "field": field.value if field else None,
            "window_days": window_days,
            "top": top,
        },
    })

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.rising_skills(
            field=field, window_days=window_days, top=top
        )

    writer({
        "type": "graph",
        "tool": "get_rising_skills",
        "chart": grouped_bar_chart(
            title=f"{field.value if field else '전체'} 최근 {result.window_days}일 언급량",
            unit="공고 수",
            series=["직전 기간", "최근 기간"],
            rows=[
                (item.skill, [item.previous_count, item.recent_count])
                for item in result.items
            ],
        ),
    })

    return {
        "window_days": result.window_days,
        "recent_postings": result.recent_postings,
        "previous_postings": result.previous_postings,
        "low_confidence": result.low_confidence,
        "newcomers": [
            {
                "rank": item.rank,
                "name": item.skill,
                "recent": item.posting_count,
                "share": item.share
            }
            for item in result.newcomers
        ],
        # rank 는 표본 크기까지 반영한 순위다. 증감률로 다시 세우면 안 된다
        "skills": [
            {
                "rank": item.rank,
                "name": item.skill,
                "recent": item.recent_count,
                "previous": item.previous_count,
                "growth_rate": item.growth_rate,
            }
            for item in result.items
        ],
    }


@tool(
    "compare_segments",
    description=(
        "회사 규모(size) · 경력(career) · 지역(location) 별로 많이 요구하는 기술을 비교한다. "
        "'스타트업과 대기업 스택 차이' 같은 질문에 쓴다."
    ),
)
async def compare_segments(
    group_by: Literal["size", "career", "location"] = "size",
    field: Optional[TechField] = None,
    top: int = 8,
    days: int = 30
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({
        "type": "tool_start",
        "tool": "compare_segments",
        "label": "구간별로 나눠 보고 있어요",
        "arguments": {
            "group_by": group_by,
            "field": field.value if field else None,
            "top": top,
            "days": days,
        },
    })

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.stacks_by_segment(
            group_by=group_by, field=field, top=top, days=days
        )

    writer({
        "type": "graph",
        "tool": "compare_segments",
        "chart": table_chart(
            title=f"{group_by} 별 상위 기술",
            columns=["구간", "기술", "공고 수"],
            rows=[
                [segment.segment, skill.skill, skill.posting_count]
                for segment in result.segments
                for skill in segment.skills
            ],
        ),
    })

    return {
        "group_by": result.group_by,
        "days": days,
        "segments": [
            {
                "segment": segment.segment,
                "postings": segment.posting_count,
                "skills": [
                    {"name": skill.skill, "postings": skill.posting_count}
                    for skill in segment.skills
                ],
            }
            for segment in result.segments
        ],
    }


@tool(
    "get_salary_stats",
    description=(
        "연봉 통계(중앙값·사분위)를 돌려준다. 공개된 공고만 집계하므로 표본이 작을 수 있다. "
        "skill 을 주면 그 기술을 요구하는 공고만 본다."
    ),
)
async def get_salary_stats(
    field: Optional[TechField] = None,
    size_type: Optional[CompanySize] = None,
    career_level: Optional[CareerLevel] = None,
    skill: Optional[str] = None
) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({
        "type": "tool_start",
        "tool": "get_salary_stats",
        "label": "연봉 분포를 계산하고 있어요",
        "arguments": {
            "field": field.value if field else None,
            "size_type": size_type.value if size_type else None,
            "career_level": career_level.value if career_level else None,
            "skill": skill,
        },
    })

    runtime = get_runtime(ToolContext)
    async with runtime.context.queries() as queries:
        result = await queries.salary_stats(
            field=field,
            size_type=size_type,
            career_level=career_level,
            skill=skill
        )

    if result.breakdown:
        writer({
            "type": "graph",
            "tool": "get_salary_stats",
            "chart": bar_chart(
                title="연봉 구간별 공고 수",
                unit="공고 수",
                rows=list(result.breakdown.items()),
            ),
        })

    return {
        "unit": result.unit,
        "sample_size": result.sample_size,
        "total_postings": result.total_postings,
        "disclosure_rate": result.disclosure_rate,
        "low_confidence": result.low_confidence,
        "median": result.median,
        "q1": result.q1,
        "q3": result.q3,
        "min": result.minimum,
        "max": result.maximum,
        "breakdown": result.breakdown,
    }