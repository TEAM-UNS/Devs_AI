from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.domains.chat.enums import Period


def _midnight() -> datetime:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def resolve(period: Period) -> tuple[datetime, datetime]:
    midnight = _midnight()
    this_monday = midnight - timedelta(days=midnight.weekday())
    this_month = midnight.replace(day=1)

    if period is Period.WEEK:
        return this_monday - timedelta(days=7), this_monday

    if period is Period.PREVIOUS_WEEK:
        return this_monday - timedelta(days=14), this_monday - timedelta(days=7)

    if period is Period.MONTH:
        return (this_month - timedelta(days=1)).replace(day=1), this_month

    last_month = (this_month - timedelta(days=1)).replace(day=1)
    return (last_month - timedelta(days=1)).replace(day=1), last_month


def preceding(period: Period) -> tuple[datetime, datetime]:
    return {
        Period.WEEK: resolve(Period.PREVIOUS_WEEK),
        Period.PREVIOUS_WEEK: _shift_week(resolve(Period.PREVIOUS_WEEK)),
        Period.MONTH: resolve(Period.PREVIOUS_MONTH),
        Period.PREVIOUS_MONTH: _shift_month(resolve(Period.PREVIOUS_MONTH)),
    }[period]


def span_label(window: tuple[datetime, datetime]) -> str:
    since, until = window
    return f"{since:%m/%d}~{until - timedelta(days=1):%m/%d}"


def _shift_week(window: tuple[datetime, datetime]) -> tuple[datetime, datetime]:
    since, until = window
    return since - timedelta(days=7), until - timedelta(days=7)


def _shift_month(window: tuple[datetime, datetime]) -> tuple[datetime, datetime]:
    since, _ = window
    return (since - timedelta(days=1)).replace(day=1), since