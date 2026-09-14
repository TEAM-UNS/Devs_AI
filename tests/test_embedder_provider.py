# 임베더 선택 분기 테스트

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.llm.embed.embed_adapter import build_embedder
from app.llm.exceptions import UpstreamError
from app.llm.embed.fake import FakeEmbedder
from app.llm.embed.gemini_embed_adapter import GeminiEmbedder


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    def _set(**kwargs: str) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", kwargs.pop("GOOGLE_API_KEY", "test-key"))
        for key, value in kwargs.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    return _set


def test_gemini_selects_gemini_embedder(env) -> None:
    env(EMBED_PROVIDER="gemini")
    assert isinstance(build_embedder(), GeminiEmbedder)


def test_fake_selects_fake_embedder(env) -> None:
    env(EMBED_PROVIDER="fake")
    assert isinstance(build_embedder(), FakeEmbedder)


def test_gemini_without_key_fails_loudly(env) -> None:
    env(EMBED_PROVIDER="gemini", GOOGLE_API_KEY="")
    with pytest.raises(UpstreamError, match="GOOGLE_API_KEY"):
        build_embedder()


def test_auto_uses_gemini_when_key_present(env) -> None:
    env(EMBED_PROVIDER="auto", GOOGLE_API_KEY="test-key")
    assert isinstance(build_embedder(), GeminiEmbedder)


def test_auto_falls_back_to_fake_without_key(env) -> None:
    env(EMBED_PROVIDER="auto", GOOGLE_API_KEY="")
    assert isinstance(build_embedder(), FakeEmbedder)


@pytest.mark.parametrize("provider", ["auto", "gemini", "fake"])
def test_force_fake_overrides_provider(env, provider: str) -> None:
    env(EMBED_PROVIDER=provider)
    assert isinstance(build_embedder(force_fake=True), FakeEmbedder)


def test_unknown_provider_is_rejected(env) -> None:
    with pytest.raises(Exception, match="embed_provider|EMBED_PROVIDER"):
        env(EMBED_PROVIDER="voyage")
        get_settings()
