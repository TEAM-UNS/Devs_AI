# 멈춘 수집 실행 정리 테스트

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.database import get_worker_session
from app.domains.market import enums, repository

MARK = "테스트-stale-run"


@pytest.fixture
async def cleanup(db):
    yield
    async with get_worker_session() as session:
        await session.exec(
            text("DELETE FROM market.crawl_run WHERE keyword = :kw").bindparams(kw=MARK)
        )


async def _make_run(*, minutes_ago: int, status: enums.RunStatus) -> int:
    async with get_worker_session() as session:
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
    async with get_worker_session() as session:
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

    async with get_worker_session() as session:
        reaped = await repository.fail_stale_runs(session, older_than_seconds=1200)

    assert reaped >= 1
    status, has_finished_at = await _status(run_id)
    assert status == "failed"
    assert has_finished_at, "finished_at 을 안 채우면 소요시간 쿼리가 NULL 이 된다"


async def test_recent_running_row_is_left_alone(cleanup) -> None:
    run_id = await _make_run(minutes_ago=1, status=enums.RunStatus.RUNNING)

    async with get_worker_session() as session:
        await repository.fail_stale_runs(session, older_than_seconds=1200)

    status, _ = await _status(run_id)
    assert status == "running"


async def test_finished_rows_are_untouched(cleanup) -> None:
    run_id = await _make_run(minutes_ago=60, status=enums.RunStatus.SUCCESS)

    async with get_worker_session() as session:
        await repository.fail_stale_runs(session, older_than_seconds=1200)

    status, _ = await _status(run_id)
    assert status == "success"
