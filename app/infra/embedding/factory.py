import logging

from app.core.config import get_settings
from app.infra.embedding.port import EmbedderPort


log = logging.getLogger(__name__)


def build_embedder(*, force_fake: bool = False) -> EmbedderPort:
    from app.infra.embedding.adapters.fake import FakeEmbedder

    settings = get_settings()

    if force_fake or settings.embed_provider == "fake":
        log.warning("FakeEmbedder 를 사용합니다 (force_fake=%s).", force_fake)
        return FakeEmbedder(dim=settings.embed_dim)

    if settings.embed_provider == "ollama":
        from app.infra.embedding.adapters.ollama_local import OllamaEmbedder

        log.info("OllamaEmbedder 를 사용합니다 (model=%s).", settings.embed_ollama_model)
        return OllamaEmbedder()

    from app.infra.embedding.adapters.api import GeminiEmbedder

    log.info("GeminiEmbedder 를 사용합니다 (model=%s).", settings.gemini_embed_model)
    return GeminiEmbedder()
