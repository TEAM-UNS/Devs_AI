# 로컬 BGE-m3 임베딩 어댑터

import asyncio
import logging
import os
from collections.abc import Sequence
from typing import Optional, TYPE_CHECKING, Any

from app.core.exception.exceptions import UpstreamError

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)


def detect_device() -> str:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - 설치 안내가 목적
        raise UpstreamError(
            "torch 가 설치돼 있지 않습니다. `uv sync --group local` 로 설치하세요."
        ) from exc

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class LocalEmbedder:
    def __init__(
        self,
        *,
        model: Optional[str] = None,
        dim: Optional[int] = None,
        device: Optional[str] = None,
        batch_size: Optional[int] = None,
        use_fp16: Optional[bool] = None,
    ) -> None:
        # gemini 벡터와 섞으면 차원은 같아도 코사인이 조용히 깨진다
        self.model = model or os.getenv("EMBED_LOCAL_MODEL") or "BAAI/bge-m3"
        self.dim = dim or int(os.getenv("EMBED_DIM") or 1024)
        self.batch_size = batch_size or int(os.getenv("EMBED_LOCAL_BATCH_SIZE") or 0) or 32
        self._device = device or os.getenv("EMBED_LOCAL_DEVICE") or None
        if use_fp16 is None:
            use_fp16 = (os.getenv("EMBED_LOCAL_FP16") or "true").lower() != "false"
        self._use_fp16 = use_fp16

        self._loaded: Optional[SentenceTransformer] = None
        self._lock = asyncio.Lock()

        self.call_count = 0
        self.total_texts = 0

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed(list(texts))

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text])
        return vectors[0]

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        async with self._lock:
            if self._loaded is None:
                await asyncio.to_thread(self._load)
            vectors = await asyncio.to_thread(self._encode, texts)

        self.call_count += 1
        self.total_texts += len(texts)
        return self._validate(vectors, expected=len(texts))

    def _load(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise UpstreamError(
                "sentence-transformers 가 설치돼 있지 않습니다. "
                "`uv sync --group local` 로 설치하세요."
            ) from exc

        device = self._device or detect_device()
        log.info("로컬 임베딩 모델 로드: %s (device=%s)", self.model, device)

        try:
            model = SentenceTransformer(self.model, device=device)
        except Exception as exc:
            raise UpstreamError(
                f"로컬 임베딩 모델 로드 실패 ({self.model}, device={device}): {exc}"
            ) from exc

        if self._use_fp16 and device in ("mps", "cuda"):
            model.half()
            log.info("fp16 활성화 (fp32 대비 약 3.1배)")

        self._device = device
        self._loaded = model
        log.info(
            "로컬 임베딩 준비 완료 — batch_size=%d max_seq_length=%s",
            self.batch_size,
            model.max_seq_length,
        )

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model: Any = self._loaded
        array = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [row.tolist() for row in array.astype("float32")]

    def _validate(self, vectors: list[list[float]], *, expected: int) -> list[list[float]]:
        if len(vectors) != expected:
            raise UpstreamError(f"임베딩 개수 불일치: 요청 {expected} vs 응답 {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dim:
                raise UpstreamError(
                    f"임베딩 차원 불일치: {len(vector)} != {self.dim}. "
                    f"모델({self.model})과 init.sql 의 vector(N) 을 맞추세요."
                )
        return vectors
