# 테스트용 가짜 임베더

import hashlib
import math
import struct
from collections.abc import Sequence

from app.core.config import get_settings
from typing import Optional


class FakeEmbedder:
    def __init__(self, dim: Optional[int] = None) -> None:
        self.dim = dim or get_settings().embed_dim
        self.call_count = 0
        self.embedded_texts: list[str] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.call_count += 1
        self.embedded_texts.extend(texts)
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.call_count += 1
        self.embedded_texts.append(text)
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        needed = self.dim * 4
        seed = text.encode("utf-8")
        buf = bytearray()
        counter = 0
        while len(buf) < needed:
            buf += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            counter += 1

        raw = struct.unpack(f">{self.dim}I", bytes(buf[:needed]))
        values = [(v / 2**31) - 1.0 for v in raw]

        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0:
            return [0.0] * self.dim
        return [v / norm for v in values]
