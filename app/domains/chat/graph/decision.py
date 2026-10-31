from functools import cache
from typing import Any, Optional
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage
)
from langchain_core.messages.tool import tool_call
from langchain_core.tools import BaseTool

from langfuse.types import TraceContext
from typesafe_sdk import Choice, Noul, Questions

from app.core.config import get_settings
from app.domains.chat.enums import Period
from app.domains.chat.schemas import UserProfile
from app.domains.chat.tools.registry import chat_tools
from app.domains.crawler.enums import (
    CareerLevel,
    CompanySize,
    Requirement,
    TechField
)
from app.domains.crawler.seed_data import skill_catalog
from app.infra.decision.jev import ask


async def decide(
    messages: list[AnyMessage],
    profile: Optional[UserProfile],
    trace_context: Optional[TraceContext] = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.jev_enabled:
        return {}

    question = messages[-1].text

    answers = await ask(
        _state(messages, profile), jev_questions(), trace_context
    )
    if answers is None:
        return {}
    if answers["multi_tool"].noul > settings.jev_multi_tool_max:
        return {}
    if answers["tool"].confidence < settings.jev_confidence_min:
        return {}

    name = answers["tool"].choice
    if name == "none":
        return {}

    tool = _tools()[name]
    args = _args(tool, answers)

    if "query" in tool.args:
        args["query"] = question
    elif not set(tool.args) <= set(jev_questions()) or not _required(tool) <= set(args):
        return {"narrow_tool": name}

    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    tool_call(
                        name=name,
                        args=args,
                        id=f"jev_{uuid4().hex}"
                    )
                ],
            )
        ]
    }


def _state(messages: list[AnyMessage], profile: Optional[UserProfile]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    
    if len(messages) > 1:
        state["history"] = [
            {
                "role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.text
            }
            for m in messages[:-1]
        ]
    state["user_message"] = messages[-1].text
    if profile is not None:
        state["user_profile"] = {
            "career_level": profile.career_level.value if profile.career_level else None,
            "fields": profile.fields,
            "skills": profile.skills,
        }
    return state


def _args(tool: BaseTool, answers: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for arg in tool.args:
        answer = answers.get(arg)
        if answer is None or answer.choice == "none":
            continue
        args[arg] = int(answer.choice) if answer.choice.isdigit() else answer.choice
    return args


def _required(tool: BaseTool) -> set[str]:
    return set(tool.tool_call_schema.model_json_schema().get("required", []))


@cache
def _tools() -> dict[str, BaseTool]:
    return {t.name: t for t in chat_tools()}


def _with_none(options: dict[str, Optional[str]], none_text: str) -> dict[str, Optional[str]]:
    return {**options, "none": none_text}


@cache
def jev_questions() -> Questions:
    return {
        "tool": Choice(
            instructions=(
                "user_message 에 답하려면 어떤 툴을 불러야 하는가? "
                "'그럼 프론트는?' 처럼 생략된 질문이면 history 에서 이어지는 의도로 판단한다"
            ),
            criteria=_with_none(
                {t.name: t.description for t in chat_tools()},
                "툴이 필요 없거나 우리 데이터로 답할 수 없다. 인사, 잡담, 챗봇 사용법, "
                "국내 IT 채용과 무관한 질문, 해외 채용, 특정 회사의 연봉처럼 툴에 없는 조건",
            ),
        ),
        "field": Choice(
            instructions=(
                "user_message 에 직군을 가리키는 말(백엔드, 프론트엔드, 모바일, 데이터, 데브옵스, "
                "보안, 게임, 임베디드)이 직접 나오면 그 직군. 생략된 질문이면 history 에서 이어지는 직군. "
                "기술 이름만 있으면 none"
            ),
            criteria=_with_none({f.value: None for f in TechField}, "직군을 가리키는 말이 없음"),
        ),
        "skill": Choice(
            instructions="user_message 에 언급된 기술 이름 하나. 생략된 질문이면 history 에서 이어지는 기술",
            criteria=_with_none({s.name: None for s in skill_catalog()}, "기술을 말하지 않음"),
        ),
        "period": Choice(
            instructions="user_message 에서 말한 달력 기간. history 에서 이어받지 않는다",
            criteria=_with_none(
                {
                    Period.WEEK.value: "'이번 주'. 직전 완료 주(월~일)",
                    Period.MONTH.value: "'이번 달'. 직전 완료 월",
                    Period.PREVIOUS_WEEK.value: "그보다 한 주 전",
                    Period.PREVIOUS_MONTH.value: "그보다 한 달 전",
                },
                "달력 기간을 말하지 않음. '요즘' · '최근 3개월' 도 여기",
            ),
        ),
        "group_by": Choice(
            instructions="무엇을 기준으로 나눠 비교하자는가",
            criteria=_with_none(
                {"size": "회사 규모", "career": "경력", "location": "지역"},
                "나눠 비교하지 않음",
            ),
        ),
        "career_level": Choice(
            instructions="사용자가 말한 경력 수준",
            criteria=_with_none({c.value: None for c in CareerLevel}, "경력을 말하지 않음"),
        ),
        "size_type": Choice(
            instructions="사용자가 말한 회사 규모",
            criteria=_with_none(
                {c.value: None for c in CompanySize if c != CompanySize.UNKNOWN},
                "규모를 말하지 않음",
            ),
        ),
        "requirement": Choice(
            instructions="요구 강도 조건",
            criteria=_with_none(
                {
                    Requirement.REQUIRED.value: "필수 조건만",
                    Requirement.PREFERRED.value: "우대 조건만",
                },
                "조건을 말하지 않음",
            ),
        ),
        "top": Choice(
            instructions="몇 개를 보여 달라고 했나",
            criteria=_with_none(
                {"3": None, "5": None, "10": None, "15": None, "20": None},
                "개수를 말하지 않음",
            ),
        ),
        "days": Choice(
            instructions="'최근 N일 · N개월' 처럼 말한 집계 기간",
            criteria=_with_none(
                {"7": "1주", "30": "1개월", "90": "3개월", "180": "6개월"}, "말하지 않음"
            ),
        ),
        "window_days": Choice(
            instructions="'N주 사이에 급상승' 처럼 증감을 비교할 구간",
            criteria=_with_none({"7": "1주", "14": "2주", "30": "1개월"}, "말하지 않음"),
        ),
        "multi_tool": Noul(
            instructions=(
                "이 메시지에 답하려면 성격이 다른 정보를 두 가지 이상 따로 조회해야 하는가? "
                "예: '수요랑 연관 기술 둘 다', '요구 기술이랑 비슷한 회사도', '인기 기술이랑 연봉 같이'. "
                "'네이버랑 카카오 비교', '대기업이랑 스타트업 비교', '신입이랑 경력 차이' 처럼 "
                "같은 기준으로 여러 대상을 한 번에 비교하는 질문은 아니오"
            ),
        ),
    }