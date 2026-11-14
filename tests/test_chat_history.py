# 대화 히스토리 변환 테스트

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.domains.chat.enums import MessageRole
from app.domains.chat.graph.prompts import off_topic_reply
from app.domains.chat.service import _to_messages


def _row(role: MessageRole, content: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content)


def test_off_topic_pair_is_dropped() -> None:
    rows = [
        _row(MessageRole.USER, "백엔드 인기 기술은?"),
        _row(MessageRole.ASSISTANT, "1. Java ..."),
        _row(MessageRole.USER, "소갈비찜 레시피 알려줘"),
        _row(MessageRole.ASSISTANT, off_topic_reply()),
    ]

    assert _to_messages(rows) == [HumanMessage("백엔드 인기 기술은?"), AIMessage("1. Java ...")]


def test_off_topic_reply_cut_by_window_is_dropped_alone() -> None:
    rows = [
        _row(MessageRole.ASSISTANT, off_topic_reply()),
        _row(MessageRole.USER, "프론트는?"),
    ]

    assert _to_messages(rows) == [HumanMessage("프론트는?")]