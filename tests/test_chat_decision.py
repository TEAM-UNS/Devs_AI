# Jev 판단 → 툴 호출 변환 테스트

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.core.config import get_settings
from app.domains.chat.graph import decision
from app.domains.chat.graph.nodes import jev_routed
from app.domains.chat.schemas import UserProfile
from app.domains.crawler.enums import CareerLevel


def _answers(tool: str, confidence: float = 0.95, multi: float = 0.1, **args: str) -> dict[str, Any]:
    answers: dict[str, Any] = {k: SimpleNamespace(choice=v, confidence=0.99) for k, v in args.items()}
    answers["tool"] = SimpleNamespace(choice=tool, confidence=confidence)
    answers["multi_tool"] = SimpleNamespace(noul=multi)
    return answers


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(get_settings(), "jev_enabled", True)
    holder: dict[str, Any] = {"calls": 0}

    async def fake_ask(state: dict[str, Any], questions: Any, trace_context: Any = None) -> Any:
        holder["calls"] += 1
        holder["state"] = state
        return holder.get("answers")

    monkeypatch.setattr(decision, "ask", fake_ask)
    return holder


async def _decide(question: str = "질문") -> dict[str, Any]:
    return await decision.decide([HumanMessage(question)], None)


async def test_confident_choice_args_go_straight_to_tools(jev: dict[str, Any]) -> None:
    jev["answers"] = _answers("get_popular_skills", field="backend", top="5", period="none", size_type="startup")

    update = await _decide()

    call = update["messages"][0].tool_calls[0]
    assert call["name"] == "get_popular_skills"
    assert call["args"] == {"field": "backend", "top": 5}, "none 과 툴에 없는 인자는 빠지고 숫자는 int"
    assert call["id"].startswith("jev_")


async def test_search_uses_question_as_query(jev: dict[str, Any]) -> None:
    jev["answers"] = _answers("search_postings", field="backend")

    update = await _decide("재택 되는 백엔드 공고")

    assert update["messages"][0].tool_calls[0]["args"] == {"field": "backend", "query": "재택 되는 백엔드 공고"}


@pytest.mark.parametrize(
    "tool",
    ["get_company_profile", "compare_companies", "get_similar_companies", "analyze_skill_gap"],
)
async def test_free_text_tools_go_to_narrow_gemini(jev: dict[str, Any], tool: str) -> None:
    jev["answers"] = _answers(tool)

    assert await _decide() == {"narrow_tool": tool}


async def test_missing_required_skill_goes_to_narrow_gemini(jev: dict[str, Any]) -> None:
    jev["answers"] = _answers("get_skill_demand", skill="none")

    assert await _decide() == {"narrow_tool": "get_skill_demand"}


async def test_skill_tool_with_skill_stays_on_jev(jev: dict[str, Any]) -> None:
    jev["answers"] = _answers("get_skill_demand", skill="Kotlin")

    update = await _decide()

    assert update["messages"][0].tool_calls[0]["args"] == {"skill": "Kotlin"}


@pytest.mark.parametrize(
    "answers",
    [
        _answers("get_popular_skills", confidence=0.6),
        _answers("get_popular_skills", multi=0.8),
        _answers("none"),
        None,
    ],
    ids=["low_confidence", "multi_tool", "no_tool", "jev_failed"],
)
async def test_uncertain_falls_back_to_gemini(jev: dict[str, Any], answers: Any) -> None:
    jev["answers"] = answers

    assert await _decide() == {}


async def test_disabled_skips_jev(jev: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "jev_enabled", False)

    assert await _decide() == {}
    assert jev["calls"] == 0


async def test_follow_up_turn_sends_history(jev: dict[str, Any]) -> None:
    jev["answers"] = _answers("get_popular_skills", field="frontend")
    messages = [HumanMessage("백엔드에서 많이 쓰는 기술은?"), AIMessage("1. Java ..."), HumanMessage("프론트엔드는?")]

    update = await decision.decide(messages, None)

    assert jev["state"]["history"] == [
        {"role": "user", "content": "백엔드에서 많이 쓰는 기술은?"},
        {"role": "assistant", "content": "1. Java ..."},
    ]
    assert jev["state"]["user_message"] == "프론트엔드는?"
    assert update["messages"][0].tool_calls[0]["args"] == {"field": "frontend"}


async def test_first_turn_has_no_history_key(jev: dict[str, Any]) -> None:
    jev["answers"] = _answers("none")

    await _decide("백엔드 인기 기술")

    assert jev["state"] == {"user_message": "백엔드 인기 기술"}


async def test_profile_is_sent_without_name(jev: dict[str, Any]) -> None:
    profile = UserProfile(
        user_id=1,
        name="홍길동",
        career_level=CareerLevel.JUNIOR,
        fields=["backend"],
        skills=["Java"],
    )

    await decision.decide([HumanMessage("내가 뭘 더 배워야 해")], profile)

    assert jev["state"]["user_profile"] == {"career_level": "junior", "fields": ["backend"], "skills": ["Java"]}


def test_routed() -> None:
    call = {"name": "get_data_coverage", "args": {}, "id": "jev_1", "type": "tool_call"}
    assert jev_routed({"messages": [AIMessage(content="", tool_calls=[call])]}) == "tools"
    assert jev_routed({"messages": [HumanMessage("안녕")]}) == "gemini"
