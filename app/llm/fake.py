"""테스트용 어댑터 — API 키 불필요, 외부 호출 없음.

FakeLLM
    미리 지정한 응답/툴호출 시퀀스를 그대로 흘려보낸다.
    툴 루프·스트리밍 이벤트 순서 테스트에 사용.
    ★ LLMPort 와 함께 챗봇 작업에서 구현한다.

FakeEmbedder
    텍스트 해시 기반 결정적 벡터를 EMBED_DIM 길이로 생성.
    같은 입력 → 같은 벡터. 코사인 유사도 테스트가 재현 가능해진다.

USE_FAKE_LLM=true 이면 DI 컨테이너가 이 구현을 주입한다.
"""

import hashlib
import math
import struct
from collections.abc import Sequence

from app.core.config import get_settings

Vector = list[float]


class FakeEmbedder:
    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or get_settings().embed_dim
        self.call_count = 0
        self.embedded_texts: list[str] = []

    # ── EmbedderPort ──────────────────────────────────────────────────────
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        self.call_count += 1
        self.embedded_texts.extend(texts)
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> Vector:
        self.call_count += 1
        self.embedded_texts.append(text)
        return self._vector(text)

    # ── 내부 ──────────────────────────────────────────────────────────────
    def _vector(self, text: str) -> Vector:
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
