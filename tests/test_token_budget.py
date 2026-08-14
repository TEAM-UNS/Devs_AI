"""토큰 추정 보정 — TPM 이 병목일 때 추정 오차 = 처리량 손실.

예약은 추정치로 하는데 한도는 실제 토큰으로 걸린다. 36% 과대 추정하면
10K TPM 중 6.4K 만 쓰게 된다. 그래서 응답의 total_tokens 로 계속 보정한다.

비대칭이 핵심이다.
    과소 추정 → 429 → 1분 손실   (즉시 크게 올려야 한다)
    과대 추정 → 조금 느림        (천천히 내려도 된다)
"""

from __future__ import annotations

from app.llm.gemini_embed_adapter import (
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
    """★ 초과 위험은 한 번에 반영한다. 안 그러면 429 가 반복된다."""
    budget = _TokenBudget(initial=0.5)
    texts = ["가" * 1000]

    budget.observe(texts, actual_tokens=900)  # 실제 0.9 토큰/자

    assert budget.ratio >= 0.9
    assert budget.estimate(texts) >= 900


def test_overestimate_decays_slowly() -> None:
    """한 번의 짧은 배치로 확 낮추지 않는다."""
    budget = _TokenBudget(initial=0.85)
    texts = ["가" * 1000]

    budget.observe(texts, actual_tokens=600)  # 실제 0.6

    assert budget.ratio < 0.85, "내려가긴 해야 한다"
    assert budget.ratio > 0.66, "한 번에 관측값까지 떨어지면 안 된다"


def test_converges_toward_observed_with_margin() -> None:
    """반복 관측하면 관측값 × 안전계수 근처로 수렴한다."""
    budget = _TokenBudget(initial=0.85)
    texts = ["가" * 1000]

    for _ in range(50):
        budget.observe(texts, actual_tokens=625)  # 실측 0.625

    assert 0.625 * TOKEN_SAFETY - 0.01 <= budget.ratio <= 0.625 * TOKEN_SAFETY + 0.01


def test_never_goes_below_the_floor() -> None:
    budget = _TokenBudget(initial=0.85)
    for _ in range(200):
        budget.observe(["가" * 1000], actual_tokens=1)

    assert budget.ratio >= TOKENS_PER_CHAR_FLOOR


def test_ignores_garbage_observations() -> None:
    """빈 입력이나 토큰 0 응답으로 비율이 망가지면 안 된다."""
    budget = _TokenBudget(initial=0.7)

    budget.observe([], actual_tokens=100)
    budget.observe(["가" * 100], actual_tokens=0)

    assert budget.ratio == 0.7
    assert budget.samples == 0


def test_estimate_scales_with_length() -> None:
    budget = _TokenBudget(initial=0.6)
    assert budget.estimate(["가" * 100]) == 60
    assert budget.estimate(["가" * 100, "나" * 100]) == 120
