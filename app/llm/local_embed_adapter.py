"""EmbedderPort 구현 — 로컬 임베딩 어댑터 (BGE-m3 / sentence-transformers).

책임
    - 모델을 프로세스당 1회 로드해 재사용 (로드에 약 6초가 든다)
    - 동기 라이브러리를 asyncio.to_thread 로 감싼다
    - 반환 벡터 차원이 EMBED_DIM(1024) 과 다르면 즉시 실패시킨다

호출 지점은 API 어댑터와 같다: crawler/embed_service.py · chat 질의 임베딩.
분기는 embed_adapter.build_embedder() 한 곳에서만 일어난다.

왜 별도 어댑터인가
    voyage 어댑터의 복잡도(레이트리밋 · 토큰 예산 보정 · 429 백오프)는 전부
    "남의 서버에 요청을 보낸다" 에서 나온다. 로컬에는 그런 게 하나도 없다.
    같은 클래스에 플래그로 섞으면 양쪽 다 읽기 어려워진다.

★ BGE-m3 는 질의/문서에 프리픽스를 붙이지 않는다.
    bge-large-en 계열은 질의에 "Represent this sentence..." 를 요구하지만
    m3 는 그런 지시문 없이 학습됐다. 그래서 embed_query 와 embed_documents 의
    처리가 동일하다 — 포트가 메서드를 나눠 둔 것은 voyage 쪽 사정이다.

★ 벡터는 voyage 와 호환되지 않는다.
    차원이 둘 다 1024 라 INSERT 는 멀쩡히 통과하는데 코사인 유사도만 조용히
    깨진다. 제공자를 바꾸면 posting_chunk.embedding 을 전량 재생성해야 한다.
    (지금은 DB 가 비어 있어 문제가 없다)

설치
    sentence-transformers 는 torch 를 끌고 와 무겁다(수 GB). 기본 의존성에
    넣지 않고 extra 로 뺐다.
        uv sync --extra local
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings
from app.core.exceptions import UpstreamError

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

Vector = list[float]

# 실측 (M5 Pro · 48GB / 192청크 / 청크 길이 150~1200자)
#     fp16 batch=32   92.0 청크/초   ← 최적
#     fp16 batch=16   91.3
#     fp16 batch=64   78.6
#     fp16 batch=96   63.4
#     fp32 batch=16   28.3
#     CPU  fp32        6.6
#
# API 어댑터의 EMBED_BATCH_SIZE(96)는 네트워크 왕복을 줄이려는 값이라 로컬에서는
# 오히려 31% 느리다. 그래서 설정을 분리했다(EMBED_LOCAL_BATCH_SIZE).
DEFAULT_BATCH_SIZE = 32

# fp16 은 fp32 대비 3.1배 빠르고 벡터는 사실상 같다.
#     같은 청크 코사인 최소 0.99976 · top-5 이웃 일치율 96.2%
# 단 CPU 에서는 half 연산이 가속되지 않아 오히려 느려진다. GPU 계열에서만 켠다.
_HALF_DEVICES = ("mps", "cuda")


def detect_device() -> str:
    """쓸 수 있는 가장 빠른 디바이스를 고른다.

    torch import 는 무겁다(수 초). 이 함수는 모델을 실제로 만들 때만 불린다.
    """
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - 설치 안내가 목적
        raise UpstreamError(
            "sentence-transformers/torch 가 설치돼 있지 않습니다. "
            "`uv sync --extra local` 로 설치하거나 EMBED_PROVIDER 를 voyage 로 두세요."
        ) from exc

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class LocalEmbedder:
    """EmbedderPort 구현. 외부 호출 없이 로컬에서 임베딩한다.

    레이트리밋도 재시도도 없다. 실패하면 그냥 실패다 — 네트워크가 없으니
    "잠시 뒤 다시" 로 나아질 여지가 없고, 재시도는 같은 예외를 반복할 뿐이다.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        dim: int | None = None,
        device: str | None = None,
        batch_size: int | None = None,
        use_fp16: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.model_name = model or settings.embed_local_model
        self.dim = dim or settings.embed_dim
        self.batch_size = batch_size or settings.embed_local_batch_size or DEFAULT_BATCH_SIZE
        self._device = device or settings.embed_local_device or None
        self._use_fp16 = settings.embed_local_fp16 if use_fp16 is None else use_fp16

        self._model: SentenceTransformer | None = None
        # 로드와 추론 둘 다 이 락으로 직렬화한다.
        #   로드 — 동시에 들어오면 6초짜리 로드를 중복으로 한다
        #   추론 — arq max_jobs=4 라 태스크 4개가 같은 모델을 동시에 부를 수
        #          있다. GPU 가 어차피 병목이라 직렬화해도 처리량 손해는 없고,
        #          torch 쪽 스레드 안전성을 신경 쓸 필요가 사라진다.
        self._lock = asyncio.Lock()

        # 검증용. voyage 어댑터와 같은 이름을 쓴다.
        self.call_count = 0
        self.total_texts = 0

    # ── EmbedderPort ──────────────────────────────────────────────────────
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        return await self._embed(list(texts))

    async def embed_query(self, text: str) -> Vector:
        # BGE-m3 는 질의/문서 구분이 없다. 모듈 docstring 참고.
        vectors = await self._embed([text])
        return vectors[0]

    # ── 내부 ──────────────────────────────────────────────────────────────
    async def _embed(self, texts: list[str]) -> list[Vector]:
        async with self._lock:
            if self._model is None:
                await asyncio.to_thread(self._load)
            # ★ 동기 호출이다. to_thread 없이 부르면 인코딩이 끝날 때까지
            #   이벤트 루프가 통째로 멈춘다(청크 수백 개면 수 초).
            vectors = await asyncio.to_thread(self._encode, texts)

        self.call_count += 1
        self.total_texts += len(texts)
        return self._validate(vectors, expected=len(texts))

    def _load(self) -> None:
        """모델을 메모리에 올린다. 프로세스당 1회, 캐시가 있으면 약 6초."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise UpstreamError(
                "sentence-transformers 가 설치돼 있지 않습니다. "
                "`uv sync --extra local` 로 설치하거나 EMBED_PROVIDER 를 voyage 로 두세요."
            ) from exc

        device = self._device or detect_device()
        log.info("로컬 임베딩 모델 로드: %s (device=%s)", self.model_name, device)

        try:
            model = SentenceTransformer(self.model_name, device=device)
        except Exception as exc:  # 다운로드 실패 · 모델명 오타 · 디바이스 불가 등
            raise UpstreamError(
                f"로컬 임베딩 모델 로드 실패 ({self.model_name}, device={device}): {exc}"
            ) from exc

        if self._use_fp16 and device in _HALF_DEVICES:
            model.half()
            log.info("fp16 활성화 (fp32 대비 약 3.1배)")

        self._device = device
        self._model = model
        log.info(
            "로컬 임베딩 준비 완료 — batch_size=%d max_seq_length=%s",
            self.batch_size,
            model.max_seq_length,
        )

    def _encode(self, texts: list[str]) -> list[Vector]:
        """동기 인코딩. 반드시 to_thread 안에서만 부른다."""
        model: Any = self._model
        array = model.encode(
            texts,
            batch_size=self.batch_size,
            # 코사인 검색(vector_cosine_ops)을 쓰므로 정규화해서 넣는다.
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        # fp16 모델은 float16 배열을 준다. pgvector 로 넘기기 전에 float32 로
        # 되돌린다 — psycopg 어댑터가 numpy float16 을 다루지 못한다.
        return [row.tolist() for row in array.astype("float32")]

    def _validate(self, vectors: list[Vector], *, expected: int) -> list[Vector]:
        """개수와 차원을 여기서 막는다. DB 까지 흘려보내지 않는다.

        voyage 어댑터에도 같은 검사가 있다. 공유하지 않고 각자 두는 이유는
        어댑터끼리 import 로 엮이지 않게 하기 위해서다 — 로컬만 쓰는 환경에서
        voyageai 를 끌고 올 이유가 없다.
        """
        if len(vectors) != expected:
            raise UpstreamError(f"임베딩 개수 불일치: 요청 {expected} vs 응답 {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dim:
                raise UpstreamError(
                    f"임베딩 차원 불일치: {len(vector)} (기대 {self.dim}). "
                    f"EMBED_LOCAL_MODEL={self.model_name} 이 vector({self.dim}) 컬럼과 "
                    "맞지 않습니다."
                )
        return vectors
