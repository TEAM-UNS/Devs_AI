"""유사 기업 점수.

점수 = 0.5 × 스택 코사인 + 0.35 × 설명 백분위 + 0.15 × 규모 근접

    스택 코사인   두 기업의 요구 스킬 벡터(스킬별 공고 수) 코사인
    설명 백분위   profile_embedding 코사인을 후보 안에서의 백분위(0~1)로 바꾼 값.
                  소개글이 없는 후보는 중립값 0.5
    규모 근접     employee_count 로그 거리. 없으면 size_type 구간 거리. 그것도 없으면 재분배

★ 설명 코사인은 0.38~0.56 좁은 띠에 몰려 있어, 원값을 섞거나 없는 쪽에 가중치를
  재분배하면 소개글이 있는 기업이 오히려 불리해진다 (실측: 알피 top10 중 소개글 있는 기업 0곳).
  업종명만으로 임베딩된 기업(71%)은 소개글 없음으로 친다.
"""

import math
from bisect import bisect_left
from collections.abc import Mapping

from app.domains.market.enums import CompanySize

WEIGHTS = (0.5, 0.35, 0.15)

NEUTRAL = 0.5

# 직원 수 10배 차이면 0.67, 1000배면 0
_LOG_SPAN = 3.0

_SIZE_ORDER: dict[str, int] = {
    size.value: index
    for index, size in enumerate(
        (
            CompanySize.STARTUP,
            CompanySize.SMALL,
            CompanySize.MEDIUM,
            CompanySize.LARGE,
            CompanySize.ENTERPRISE,
        )
    )
}


def percentile_ranks(values: Mapping[int, float]) -> dict[int, float]:
    ordered = sorted(values.values())
    span = max(len(ordered) - 1, 1)
    return {key: bisect_left(ordered, value) / span for key, value in values.items()}


def size_proximity(
    a_employees: int | None,
    a_size: str | None,
    b_employees: int | None,
    b_size: str | None,
) -> float | None:
    if a_employees and b_employees:
        gap = abs(math.log10(a_employees) - math.log10(b_employees))
        return max(0.0, 1 - gap / _LOG_SPAN)
    a = _SIZE_ORDER.get(a_size) if a_size else None
    b = _SIZE_ORDER.get(b_size) if b_size else None
    if a is None or b is None:
        return None
    return 1 - abs(a - b) / (len(_SIZE_ORDER) - 1)


def combine(stack: float | None, description: float | None, size: float | None) -> float:
    parts = [(w, v) for w, v in zip(WEIGHTS, (stack, description, size), strict=True) if v is not None]
    total = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total if total else 0.0
