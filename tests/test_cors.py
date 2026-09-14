# CORS 허용 오리진 테스트

from __future__ import annotations

import importlib

import pytest
from starlette.middleware.cors import CORSMiddleware

import app.main
from app.core.config import get_settings


def _wired_origins(monkeypatch: pytest.MonkeyPatch, value: str) -> list[str]:
    monkeypatch.setenv("CORS_ORIGINS", value)
    get_settings.cache_clear()
    importlib.reload(app.main)
    cors = next(m for m in app.main.app.user_middleware if m.cls is CORSMiddleware)
    return cors.kwargs["allow_origins"]


@pytest.fixture(autouse=True)
def _restore_app():
    yield
    get_settings.cache_clear()
    importlib.reload(app.main)


def _middleware(origins: list[str]) -> CORSMiddleware:
    return CORSMiddleware(app=None, allow_origins=origins, allow_credentials=True)


def test_comma_string_is_split_into_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _wired_origins(monkeypatch, "https://jobstack.com, https://www.jobstack.com")

    assert isinstance(origins, list)  # 문자열로 넘기면 부분문자열 매칭이 된다
    assert origins == ["https://jobstack.com", "https://www.jobstack.com"]


def test_empty_setting_allows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _wired_origins(monkeypatch, "")

    assert origins == []
    assert not _middleware(origins).allow_all_origins


@pytest.mark.parametrize(
    "origin",
    [
        "https://jobstack.co",
        "https://jobstack.c",
        "https://evil.com",
        "http://jobstack.com",
    ],
)
def test_lookalike_origins_are_rejected(origin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _wired_origins(monkeypatch, "https://jobstack.com")

    assert not _middleware(origins).is_allowed_origin(origin)


def test_configured_origin_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = _middleware(_wired_origins(monkeypatch, "https://jobstack.com, http://localhost:3000"))

    assert middleware.is_allowed_origin("https://jobstack.com")
    assert middleware.is_allowed_origin("http://localhost:3000")
