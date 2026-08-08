"""죽은 태스크가 남긴 running 행 정리 (worker.startup 이 부른다).

워커가 SIGKILL 되거나 컨테이너가 재시작되면 finish_run 이 불리지 못해
crawl_run 이 영원히 running 으로 남는다. 실제로 워커를 재시작했을 때
running 두 건이 남는 것을 보고 붙였다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core import enums
from app.core.database import session_scope
from app.domains.market import repository

MARK = "테스트-stale-run"


@pytest.fixture
async def cleanup(db):
    yield
    async with session_scope() as session:
        await session.exec(
            text("DELETE FROM market.crawl_run WHERE keyword = :kw").bindparams(kw=MARK)
        )


async def _make_run(*, minutes_ago: int, status: enums.RunStatus) -> int:
    async with session_scope() as session:
        run_id = await repository.start_run(
            session, kind=enums.RunKind.CRAWL, source="saramin", keyword=MARK
        )
        await session.exec(
            text(
                "UPDATE market.crawl_run SET started_at = :ts, status = :st WHERE id = :id"
            ).bindparams(
                ts=datetime.now(UTC) - timedelta(minutes=minutes_ago),
                st=status.value,
                id=run_id,
            )
        )
    return run_id


async def _status(run_id: int) -> tuple[str, bool]:
    async with session_scope() as session:
        row = (
            await session.exec(
                text(
                    "SELECT status, finished_at IS NOT NULL FROM market.crawl_run WHERE id = :id"
                ).bindparams(id=run_id)
            )
        ).one()
    return row[0], row[1]


async def test_old_running_row_is_failed(cleanup) -> None:
    run_id = await _make_run(minutes_ago=60, status=enums.RunStatus.RUNNING)

    async with session_scope() as session:
        reaped = await repository.fail_stale_runs(session, older_than_seconds=1200)

    assert reaped >= 1
    status, has_finished_at = await _status(run_id)
    assert status == "failed"
    assert has_finished_at, "finished_at 을 안 채우면 소요시간 쿼리가 NULL 이 된다"


async def test_recent_running_row_is_left_alone(cleanup) -> None:
    """★ 지금 돌고 있는 실행을 죽이면 안 된다.

    기준을 job_timeout 보다 길게 잡는 이유가 이것이다.
    """
    run_id = await _make_run(minutes_ago=1, status=enums.RunStatus.RUNNING)

    async with session_scope() as session:
        await repository.fail_stale_runs(session, older_than_seconds=1200)

    status, _ = await _status(run_id)
    assert status == "running"


async def test_finished_rows_are_untouched(cleanup) -> None:
    run_id = await _make_run(minutes_ago=60, status=enums.RunStatus.SUCCESS)

    async with session_scope() as session:
        await repository.fail_stale_runs(session, older_than_seconds=1200)

    status, _ = await _status(run_id)
    assert status == "success"
