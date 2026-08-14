"""_RateLimiter — 슬라이딩 윈도우 회귀 테스트.

★ 실제로 터진 버그: 요청 하나의 추정 토큰이 창 한도보다 크면 창을 전부 비운
  뒤 self._events[0] 를 읽어 IndexError 가 났다. 임베딩 318건이 이걸로 죽었다.

  경위: _split 이 비율 R1 로 배치를 잘라 tpm 에 맞췄는데, 그 사이 응답을 보고
  적응형 비율이 R2(>R1) 로 오르면 같은 배치의 재추정치가 tpm 을 넘어선다.
  대기해도 비울 게 없으니 영원히 안 풀린다 — 그냥 보내야 한다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.llm.gemini_embed_adapter import _RateLimiter


async def test_oversized_request_is_sent_instead_of_hanging() -> None:
    """★ 회귀. 창 한도보다 큰 요청이 IndexError 를 내면 안 된다."""
    limiter = _RateLimiter(rpm=3, tpm=9000)

    # 한도의 두 배짜리 요청. 기다려도 비울 게 없다.
    await asyncio.wait_for(limiter.acquire(18_000), timeout=2.0)

    assert len(limiter._events) == 1


async def test_oversized_request_on_empty_window_after_prune() -> None:
    """정확한 재현 경로: 창에 기록이 있다가 전부 만료된 뒤 초과 요청이 온다."""
    limiter = _RateLimiter(rpm=3, tpm=9000)
    await limiter.acquire(8000)
    # 창을 인위적으로 만료시킨다 (60초 지난 것처럼)
    stamp, tokens = limiter._events[0]
    limiter._events[0] = (stamp - 120.0, tokens)

    await asyncio.wait_for(limiter.acquire(20_000), timeout=2.0)

    assert len(limiter._events) == 1  # 만료분은 정리되고 새 것만 남는다


async def test_fits_within_budget_does_not_wait() -> None:
    limiter = _RateLimiter(rpm=3, tpm=9000)

    await asyncio.wait_for(limiter.acquire(3000), timeout=1.0)
    await asyncio.wait_for(limiter.acquire(3000), timeout=1.0)

    assert len(limiter._events) == 2


async def test_disabled_limiter_is_a_no_op() -> None:
    """rpm·tpm 이 0 이면(유료 등급) 계산 자체를 하지 않는다."""
    limiter = _RateLimiter(rpm=0, tpm=0)
    assert limiter.enabled is False

    await asyncio.wait_for(limiter.acquire(10_000_000), timeout=1.0)

    assert len(limiter._events) == 0


async def test_prune_drops_expired_entries() -> None:
    limiter = _RateLimiter(rpm=3, tpm=9000)
    await limiter.acquire(1000)
    stamp, tokens = limiter._events[0]
    limiter._events[0] = (stamp - 120.0, tokens)

    limiter._prune(__import__("time").monotonic())

    assert len(limiter._events) == 0


@pytest.mark.parametrize(("rpm", "tpm"), [(3, 0), (0, 9000)])
async def test_single_limit_configurations(rpm: int, tpm: int) -> None:
    """한쪽만 켜도 동작해야 한다."""
    limiter = _RateLimiter(rpm=rpm, tpm=tpm)
    assert limiter.enabled is True

    await asyncio.wait_for(limiter.acquire(500), timeout=1.0)

    assert len(limiter._events) == 1
