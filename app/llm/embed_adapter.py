"""임베더 팩토리 — 어느 구현을 쓸지 고르는 단 한 곳.

여기에는 벤더 코드가 없다. 실제 구현은 gemini_embed_adapter.py · fake.py 에
있고, 이 모듈은 설정을 보고 둘 중 하나를 돌려준다. 태스크·툴은 EmbedderPort
만 보므로 제공자가 바뀌어도 호출부는 손댈 필요가 없다.

호출 지점: worker.py(기동 시 1회) · cli.py
"""

import logging

from app.core.config import get_settings
from app.llm.port import EmbedderPort

log = logging.getLogger(__name__)

__all__ = ["build_embedder"]


def build_embedder(*, force_fake: bool = False) -> EmbedderPort:
    from app.llm.fake import FakeEmbedder

    settings = get_settings()

    if force_fake or settings.embed_provider == "fake":
        log.warning("FakeEmbedder 를 사용합니다 (force_fake=%s).", force_fake)
        return FakeEmbedder(dim=settings.embed_dim)

    if settings.embed_provider == "auto" and not settings.google_api_key:
        log.warning("GOOGLE_API_KEY 가 없어 FakeEmbedder 를 사용합니다.")
        return FakeEmbedder(dim=settings.embed_dim)

    from app.llm.gemini_embed_adapter import GeminiEmbedder

    log.info("GeminiEmbedder 를 사용합니다 (model=%s).", settings.gemini_embed_model)
    return GeminiEmbedder()
