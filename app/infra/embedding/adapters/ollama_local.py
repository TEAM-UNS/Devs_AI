import logging
from collections.abc import Sequence

from ollama import AsyncClient

from app.core.config import get_settings
from app.core.exception.exceptions import UpstreamError

logger = logging.getLogger(__name__)


class OllamaEmbedder:
    def __init__(self) -> None:
        settings = get_settings()

        self.model = settings.embed_ollama_model
        self.dim = settings.embed_dim
        self.batch_size = settings.embed_batch_size
        self.host = settings.ollama_host

        self._client = AsyncClient(host=self.host)

        self.call_count = 0
        self.total_texts = 0

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(
                await self._embed(
                    list(texts[start : start + self.batch_size])
                )
            )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text])
        return vectors[0]

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.embed(
                model=self.model,
                input=texts
            )
        except Exception as exc:
            raise UpstreamError(
                f"올라마 임베딩 실패 ({self.host}, {self.model}): {exc}"
            ) from exc

        vectors = [
            list(vector)
            for vector in response.embeddings
        ]

        self.call_count += 1
        self.total_texts += len(texts)
        return self._validate(vectors, expected=len(texts))

    def _validate(
        self,
        vectors: list[list[float]],
        expected: int
    ) -> list[list[float]]:
        if len(vectors) != expected:
            raise UpstreamError(
                f"임베딩 개수 불일치: 요청 {expected} vs 응답 {len(vectors)}"
            )

        for vector in vectors:
            if len(vector) != self.dim:
                raise UpstreamError(
                    f"임베딩 차원 불일치: {len(vector)} != {self.dim}. "
                    f"모델({self.model})과 EMBED_DIM 을 맞추세요."
                )

        return vectors
