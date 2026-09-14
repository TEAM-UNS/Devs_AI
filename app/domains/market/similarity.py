# 유사 기업 점수 계산

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


# 설명 코사인은 좁은 띠에 몰려 있어 원값 대신 후보 안 백분위로 쓴다
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
