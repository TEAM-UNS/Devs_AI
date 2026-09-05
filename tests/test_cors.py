"""CORS 오리진 허용 범위.

★ 이 파일이 있는 이유 — 개발 환경에서는 절대 드러나지 않는 버그가 있었다.

    allow_origins 에 CORS_ORIGINS(콤마로 이은 **문자열**)를 그대로 넘겼다.
    Starlette 의 검사는 `return origin in self.allow_origins` 인데, 문자열에
    in 을 쓰면 부분문자열 매칭이 된다.

        "https://jobstack.co" in "https://jobstack.com"   → True

    즉 공격자가 살 수 있는 유사 도메인이 통과한다. allow_credentials=True 라
    그 오리진이 인증 토큰 실린 요청을 보내고 응답까지 읽는다.

    개발 중에는 CORS_ORIGINS 가 localhost 두 개뿐이고 서로 부분문자열이라
    정상 동작한 것처럼 보인다. 운영 도메인을 넣는 순간 구멍이 열린다.

여기서 못 박는 것은 "main.py 가 리스트로 쪼개서 미들웨어에 넣는가" 다.
"""

from __future__ import annotations

import importlib

import pytest
from starlette.middleware.cors import CORSMiddleware

import app.main
from app.core.config import get_settings


def _wired_origins(monkeypatch: pytest.MonkeyPatch, value: str) -> list[str]:
    """CORS_ORIGINS 를 주고, main.py 가 실제로 미들웨어에 넣은 값을 꺼낸다."""
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
    """앱과 같은 인자로 미들웨어를 만든다 (allow_credentials 포함)."""
    return CORSMiddleware(app=None, allow_origins=origins, allow_credentials=True)


def test_comma_string_is_split_into_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _wired_origins(monkeypatch, "https://jobstack.com, https://www.jobstack.com")

    assert isinstance(origins, list)  # ★ 문자열이면 부분문자열 매칭이 된다
    assert origins == ["https://jobstack.com", "https://www.jobstack.com"]


def test_empty_setting_allows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """미설정이 '전체 허용' 으로 흐르면 안 된다."""
    origins = _wired_origins(monkeypatch, "")

    assert origins == []
    assert not _middleware(origins).allow_all_origins


# ── ★ 회귀: 유사 도메인 차단 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "origin",
    [
        "https://jobstack.co",  # 부분문자열 — 문자열로 넘기면 통과했다
        "https://jobstack.c",
        "https://evil.com",
        "http://jobstack.com",  # 스킴이 다르다
    ],
)
def test_lookalike_origins_are_rejected(origin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    origins = _wired_origins(monkeypatch, "https://jobstack.com")

    assert not _middleware(origins).is_allowed_origin(origin)


def test_configured_origin_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = _middleware(_wired_origins(monkeypatch, "https://jobstack.com, http://localhost:3000"))

    assert middleware.is_allowed_origin("https://jobstack.com")
    assert middleware.is_allowed_origin("http://localhost:3000")
