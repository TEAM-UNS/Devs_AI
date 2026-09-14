# 토큰 추정 보정 테스트

from __future__ import annotations

from app.llm.embed.gemini_embed_adapter import (
    TOKEN_SAFETY,
    TOKENS_PER_CHAR,
    TOKENS_PER_CHAR_FLOOR,
    _TokenBudget,
)


def test_starts_from_the_conservative_default() -> None:
    budget = _TokenBudget()
    assert budget.ratio == TOKENS_PER_CHAR
    assert budget.estimate(["가" * 100]) == round(100 * TOKENS_PER_CHAR)


def test_underestimate_is_corrected_immediately() -> None:
    budget = _TokenBudget(initial=0.5)
    texts = ["가" * 1000]

    budget.observe(texts, actual_tokens=900)

    assert budget.ratio >= 0.9
    assert budget.estimate(texts) >= 900


def test_overestimate_decays_slowly() -> None:
    budget = _TokenBudget(initial=0.85)
    texts = ["가" * 1000]

    budget.observe(texts, actual_tokens=600)

    assert budget.ratio < 0.85, "내려가긴 해야 한다"
    assert budget.ratio > 0.66, "한 번에 관측값까지 떨어지면 안 된다"


def test_converges_toward_observed_with_margin() -> None:
    budget = _TokenBudget(initial=0.85)
    texts = ["가" * 1000]

    for _ in range(50):
        budget.observe(texts, actual_tokens=625)

    assert 0.625 * TOKEN_SAFETY - 0.01 <= budget.ratio <= 0.625 * TOKEN_SAFETY + 0.01


def test_never_goes_below_the_floor() -> None:
    budget = _TokenBudget(initial=0.85)
    for _ in range(200):
        budget.observe(["가" * 1000], actual_tokens=1)

    assert budget.ratio >= TOKENS_PER_CHAR_FLOOR


def test_ignores_garbage_observations() -> None:
    budget = _TokenBudget(initial=0.7)

    budget.observe([], actual_tokens=100)
    budget.observe(["가" * 100], actual_tokens=0)

    assert budget.ratio == 0.7
    assert budget.samples == 0


def test_estimate_scales_with_length() -> None:
    budget = _TokenBudget(initial=0.6)
    assert budget.estimate(["가" * 100]) == 60
    assert budget.estimate(["가" * 100, "나" * 100]) == 120
