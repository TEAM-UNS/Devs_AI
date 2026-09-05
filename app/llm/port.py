"""LLMPort · EmbedderPort — 도메인이 의존하는 유일한 인터페이스 (Protocol).

여기에는 벤더 타입이 절대 들어오지 않는다. 어댑터도 import 하지 않는다(R4).

LLMPort
    stream(messages, tools, system) -> AsyncIterator[LLMEvent]
        LLMEvent: text_delta · tool_use · tool_result_request · usage · done
    복잡한 스트리밍 원본 이벤트는 어댑터가 이 공통 이벤트로 번역한다.
    ★ 챗봇 작업에서 정의한다. 지금은 임베딩 경로만 필요해 비워 둔다.

EmbedderPort
    embed_documents(texts: Sequence[str]) -> list[list[float]]
    embed_query(text: str) -> list[float]
        - 문서/질의 입력 타입을 구분하는 모델이 있어 메서드를 나눈다
        - 차원은 settings.EMBED_DIM 과 일치해야 한다 (어댑터가 검증한다)

구현체: chat_adapter.py · embed_adapter.py · fake.py
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

Vector = list[float]


@runtime_checkable
class EmbedderPort(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        ...

    async def embed_query(self, text: str) -> Vector:
        ...
