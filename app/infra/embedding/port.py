# 임베더 포트 인터페이스

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


# 테스트가 isinstance 로 계약 준수를 확인한다
@runtime_checkable
class EmbedderPort(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    async def embed_query(self, text: str) -> list[float]:
        ...
