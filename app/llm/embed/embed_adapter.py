# 설정을 보고 임베더 구현을 골라 준다

import logging

from app.core.config import get_settings
from app.llm.embed.port import EmbedderPort

log = logging.getLogger(__name__)

__all__ = ["build_embedder"]


def build_embedder(*, force_fake: bool = False) -> EmbedderPort:
    from app.llm.embed.fake import FakeEmbedder

    settings = get_settings()

    if force_fake or settings.embed_provider == "fake":
        log.warning("FakeEmbedder 를 사용합니다 (force_fake=%s).", force_fake)
        return FakeEmbedder(dim=settings.embed_dim)

    if settings.embed_provider == "auto" and not settings.google_api_key:
        log.warning("GOOGLE_API_KEY 가 없어 FakeEmbedder 를 사용합니다.")
        return FakeEmbedder(dim=settings.embed_dim)

    from app.llm.embed.gemini_embed_adapter import GeminiEmbedder

    log.info("GeminiEmbedder 를 사용합니다 (model=%s).", settings.gemini_embed_model)
    return GeminiEmbedder()
