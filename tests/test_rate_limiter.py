# 임베딩 레이트 리미터 테스트

from __future__ import annotations

import asyncio

import pytest

from app.llm.embed.gemini_embed_adapter import _RateLimiter


async def test_oversized_request_is_sent_instead_of_hanging() -> None:
    limiter = _RateLimiter(rpm=3, tpm=9000)

    await asyncio.wait_for(limiter.acquire(18_000), timeout=2.0)

    assert len(limiter._events) == 1


async def test_oversized_request_on_empty_window_after_prune() -> None:
    limiter = _RateLimiter(rpm=3, tpm=9000)
    await limiter.acquire(8000)
    # 60초가 지난 것처럼 기록을 만료시킨다
    stamp, tokens = limiter._events[0]
    limiter._events[0] = (stamp - 120.0, tokens)

    await asyncio.wait_for(limiter.acquire(20_000), timeout=2.0)

    assert len(limiter._events) == 1


async def test_fits_within_budget_does_not_wait() -> None:
    limiter = _RateLimiter(rpm=3, tpm=9000)

    await asyncio.wait_for(limiter.acquire(3000), timeout=1.0)
    await asyncio.wait_for(limiter.acquire(3000), timeout=1.0)

    assert len(limiter._events) == 2


async def test_disabled_limiter_is_a_no_op() -> None:
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
    limiter = _RateLimiter(rpm=rpm, tpm=tpm)
    assert limiter.enabled is True

    await asyncio.wait_for(limiter.acquire(500), timeout=1.0)

    assert len(limiter._events) == 1
