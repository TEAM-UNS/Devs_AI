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

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Sequence

from app.core.enums import EMBEDDING_DIM

Vector = list[float]


class FakeEmbedder:
    """EmbedderPort 구현. 외부 호출 없이 결정적 벡터를 만든다.

    sha256 을 카운터와 함께 반복 해싱해 필요한 바이트를 얻고, 4바이트씩
    float 로 읽어 [-1, 1) 로 접은 뒤 L2 정규화한다. 정규화해 두면 코사인
    유사도가 내적과 같아져 테스트에서 값을 예측하기 쉽다.

    같은 텍스트는 항상 같은 벡터다. 따라서 chunk_hash 가 그대로면 재임베딩
    했는지 여부를 벡터만 봐서는 알 수 없다 — 그 검증은 호출 횟수로 한다
    (call_count · embedded_texts).
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        # 테스트 검증용. "배치 1회로 묶였는가", "두 번째 실행은 0건인가" 를
        # 이 두 값으로 확인한다.
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
        # 문서와 같은 공간에 놓아야 검색 테스트가 의미를 갖는다.
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
        # 2**31 로 나눠 [-1, 1) 로 접는다.
        values = [(v / 2**31) - 1.0 for v in raw]

        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0:  # 실질적으로 불가능하지만 0 나눗셈은 막는다
            return [0.0] * self.dim
        return [v / norm for v in values]
