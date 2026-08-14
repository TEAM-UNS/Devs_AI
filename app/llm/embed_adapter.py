"""임베더 팩토리 — 어느 구현을 쓸지 고르는 단 한 곳.

여기에는 벤더 코드가 없다. 실제 구현은 gemini_embed_adapter.py · fake.py 에
있고, 이 모듈은 설정을 보고 둘 중 하나를 돌려준다. 태스크·툴은 EmbedderPort
만 보므로 제공자가 바뀌어도 호출부는 손댈 필요가 없다.

호출 지점: worker.py(기동 시 1회) · cli.py
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.llm.port import EmbedderPort

log = logging.getLogger(__name__)

__all__ = ["build_embedder"]


def build_embedder(*, force_fake: bool = False) -> EmbedderPort:
    """설정에 따라 어댑터를 고른다.

    ★ 여기가 유일한 분기점이다.

    EMBED_PROVIDER
        auto    키가 있으면 gemini, 없으면 fake (기본값)
        gemini  임베딩 API (gemini-embedding-2)
        fake    해시 기반 더미 벡터

    USE_FAKE_LLM 은 보지 않는다. 저건 챗봇 LLM 스위치다. 임베딩까지 같이
    묶으면 "챗봇은 Fake 로 두고 임베딩만 실제로 한 번 돌린다" 를 못 한다.
    임베딩은 EMBED_PROVIDER 로 판단하고, 강제하고 싶으면 force_fake 를 쓴다.

    ★ 모델을 바꾸면 기존 벡터는 못 쓴다. 차원만 1024 로 맞으면 INSERT 는
      통과하지만 벡터 공간이 달라 코사인 유사도가 조용히 깨진다. 바꿨다면
      posting_chunk.embedding · company.embedding 을 전량 재생성할 것 —
      재생성 절차는 README "모델을 바꿨을 때" 참고.
    """
    # 구현은 지연 import 한다. port 만 알면 되는 모듈이 httpx 나 어댑터를
    # 끌고 올 이유가 없다.
    from app.llm.fake import FakeEmbedder

    settings = get_settings()

    if force_fake or settings.embed_provider == "fake":
        log.warning("FakeEmbedder 를 사용합니다 (force_fake=%s).", force_fake)
        return FakeEmbedder(dim=settings.embed_dim)

    # auto — 키가 없으면 fake 로 떨어진다. gemini 를 명시했으면 키가 없어도
    # 떨어지지 않고 어댑터가 UpstreamError 를 던진다. 명시한 제공자가 조용히
    # 바뀌면 더미 벡터가 DB 에 들어가고 검색만 무의미해지기 때문이다.
    if settings.embed_provider == "auto" and not settings.google_api_key:
        log.warning("GOOGLE_API_KEY 가 없어 FakeEmbedder 를 사용합니다.")
        return FakeEmbedder(dim=settings.embed_dim)

    from app.llm.gemini_embed_adapter import GeminiEmbedder

    log.info("GeminiEmbedder 를 사용합니다 (model=%s).", settings.gemini_embed_model)
    return GeminiEmbedder()
