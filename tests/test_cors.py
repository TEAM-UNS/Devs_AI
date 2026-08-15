"""CORS 오리진 허용 범위.

★ 이 파일이 있는 이유 — 개발 환경에서는 절대 드러나지 않는 버그가 있었다.

    allow_origins 에 cors_origins(콤마로 이은 **문자열**)를 그대로 넘겼다.
    Starlette 의 검사는 `return origin in self.allow_origins` 인데, 문자열에
    in 을 쓰면 부분문자열 매칭이 된다.

        "https://jobstack.co" in "https://jobstack.com"   → True

    즉 공격자가 살 수 있는 유사 도메인이 통과한다. allow_credentials=True 라
    그 오리진이 인증 토큰 실린 요청을 보내고 응답까지 읽는다.

    개발 중에는 CORS_ORIGINS 가 localhost 두 개뿐이고 서로 부분문자열이라
    정상 동작한 것처럼 보인다. 운영 도메인을 넣는 순간 구멍이 열린다.

여기서 못 박는 것은 "설정 문자열이 리스트로 쪼개져서 미들웨어에 들어가는가"다.
"""

from __future__ import annotations

import pytest
from starlette.middleware.cors import CORSMiddleware

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _middleware(origins: list[str]) -> CORSMiddleware:
    """앱과 같은 인자로 미들웨어를 만든다 (allow_credentials 포함)."""
    return CORSMiddleware(app=None, allow_origins=origins, allow_credentials=True)


# ── 설정 파싱 ───────────────────────────────────────────────────────────────
def test_comma_string_is_split_into_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://jobstack.com, https://www.jobstack.com")
    get_settings.cache_clear()

    origins = get_settings().cors_origin_list
    assert origins == ["https://jobstack.com", "https://www.jobstack.com"]
    assert isinstance(origins, list)  # ★ 문자열이면 부분문자열 매칭이 된다


def test_empty_setting_allows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """미설정이 '전체 허용' 으로 흐르면 안 된다."""
    monkeypatch.setenv("CORS_ORIGINS", "")
    get_settings.cache_clear()

    assert get_settings().cors_origin_list == []
    assert not _middleware(get_settings().cors_origin_list).allow_all_origins


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
    monkeypatch.setenv("CORS_ORIGINS", "https://jobstack.com")
    get_settings.cache_clear()

    assert not _middleware(get_settings().cors_origin_list).is_allowed_origin(origin)


def test_configured_origin_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://jobstack.com, http://localhost:3000")
    get_settings.cache_clear()

    middleware = _middleware(get_settings().cors_origin_list)
    assert middleware.is_allowed_origin("https://jobstack.com")
    assert middleware.is_allowed_origin("http://localhost:3000")


def test_the_app_wires_the_list_not_the_raw_string() -> None:
    """★ 핵심. main.py 가 cors_origins(str) 를 넘기면 여기서 잡힌다."""
    from app.main import app

    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    configured = cors.kwargs["allow_origins"]
    assert isinstance(configured, list), (
        f"allow_origins 가 {type(configured).__name__} 다. "
        "문자열이면 Starlette 이 부분문자열로 매칭해 유사 도메인이 통과한다."
    )
