from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedderPort(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    async def embed_query(self, text: str) -> list[float]:
        ...
