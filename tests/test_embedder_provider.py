"""build_embedder 의 분기 — 실제 임베더와 더미를 갈아끼우는 지점.

여기가 틀리면 조용히 망가진다. 의도와 다른 구현이 붙어도 벡터는 정상적으로
1024 차원으로 들어가고 INSERT 도 통과하기 때문이다. 나중에 검색이 이상하다는
형태로만 드러난다. 그래서 "어떤 설정에서 어떤 구현이 나오는가" 를 못 박는다.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.llm.embed_adapter import build_embedder
from app.llm.fake import FakeEmbedder
from app.llm.gemini_embed_adapter import GeminiEmbedder


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings 는 lru_cache 다. 환경변수를 바꿨으면 비워야 반영된다."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    """.env 값이 새어 들어오지 않게 임베딩 관련 변수를 고정한다."""

    def _set(**kwargs: str) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", kwargs.pop("GOOGLE_API_KEY", "test-key"))
        for key, value in kwargs.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    return _set


# ── 명시적 지정 ─────────────────────────────────────────────────────────────
def test_gemini_selects_gemini_embedder(env) -> None:
    env(EMBED_PROVIDER="gemini")
    assert isinstance(build_embedder(), GeminiEmbedder)


def test_fake_selects_fake_embedder(env) -> None:
    env(EMBED_PROVIDER="fake")
    assert isinstance(build_embedder(), FakeEmbedder)


def test_gemini_without_key_fails_loudly(env) -> None:
    """★ 명시했는데 키가 없으면 조용히 fake 로 떨어지면 안 된다.

    fake 벡터가 DB 에 들어가면 INSERT 는 통과하고 검색만 무의미해진다. 에러가
    안 나서 한참 뒤에야 드러난다.
    """
    env(EMBED_PROVIDER="gemini", GOOGLE_API_KEY="")
    with pytest.raises(UpstreamError, match="GOOGLE_API_KEY"):
        build_embedder()


# ── auto — 키 유무로 판단한다 ───────────────────────────────────────────────
def test_auto_uses_gemini_when_key_present(env) -> None:
    env(EMBED_PROVIDER="auto", GOOGLE_API_KEY="test-key")
    assert isinstance(build_embedder(), GeminiEmbedder)


def test_auto_falls_back_to_fake_without_key(env) -> None:
    """키 없이도 파이프라인 전체를 한 번 돌려 볼 수 있어야 한다."""
    env(EMBED_PROVIDER="auto", GOOGLE_API_KEY="")
    assert isinstance(build_embedder(), FakeEmbedder)


# ── force_fake 는 설정보다 우선한다 ─────────────────────────────────────────
@pytest.mark.parametrize("provider", ["auto", "gemini", "fake"])
def test_force_fake_overrides_provider(env, provider: str) -> None:
    env(EMBED_PROVIDER=provider)
    assert isinstance(build_embedder(force_fake=True), FakeEmbedder)


# ── 잘못된 값은 기동 시점에 막는다 ──────────────────────────────────────────
def test_unknown_provider_is_rejected(env) -> None:
    """Literal 이라 pydantic 이 거른다. 오타로 조용히 fake 가 되면 안 된다."""
    with pytest.raises(Exception, match="embed_provider|EMBED_PROVIDER"):
        env(EMBED_PROVIDER="voyage")
        get_settings()
