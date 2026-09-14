# arq 수집 태스크 큐잉 테스트

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.domains.crawler import tasks
from app.domains.crawler.config import (
    CRAWL_CONFIG,
    KEYWORDS,
    SITE_CLASSES,
    iter_crawl_jobs,
)


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], str | None]] = []
        self._taken: set[str] = set()

    async def enqueue_job(self, name: str, *args: Any, _job_id: str | None = None, **_: Any):
        if _job_id is not None and _job_id in self._taken:
            return None  # arq 는 중복 job_id 에 None 을 돌려준다
        if _job_id is not None:
            self._taken.add(_job_id)
        self.calls.append((name, args, _job_id))
        return object()


def test_config_expands_to_10_jobs() -> None:
    jobs = iter_crawl_jobs()
    assert len(jobs) == 10

    by_site: dict[str, int] = {}
    for job in jobs:
        by_site[job.site] = by_site.get(job.site, 0) + 1

    assert by_site == {"jumpit": 1, "wanted": 1, "saramin": 8}
    assert len(KEYWORDS) == 8


def test_jobkorea_is_out_of_the_batch_but_still_buildable() -> None:
    assert "jobkorea" not in CRAWL_CONFIG
    assert all(job.site != "jobkorea" for job in iter_crawl_jobs())
    assert "jobkorea" in SITE_CLASSES


def test_keywordless_sites_get_no_keyword() -> None:
    jobs = {j.site: j for j in iter_crawl_jobs() if j.site in {"jumpit", "wanted"}}
    assert jobs["jumpit"].keyword is None
    assert jobs["wanted"].keyword is None
    assert jobs["jumpit"].pages == CRAWL_CONFIG["jumpit"]["pages"]


async def test_dispatch_fans_out_with_dedup_job_ids() -> None:
    redis = FakeRedis()

    result = await tasks.crawl_dispatch({"redis": redis})

    assert result == {"enqueued": 10, "duplicated": 0}
    assert {name for name, _, _ in redis.calls} == {"crawl_site"}

    job_ids = [job_id for _, _, job_id in redis.calls]
    assert len(set(job_ids)) == 10
    assert all(job_id.startswith("crawl:") for job_id in job_ids)
    assert all(job_id.split(":")[-1].isdigit() for job_id in job_ids)
    assert "crawl:jumpit:all:" in next(j for j in job_ids if j.startswith("crawl:jumpit"))


async def test_dispatch_twice_is_blocked_by_job_id() -> None:
    redis = FakeRedis()

    await tasks.crawl_dispatch({"redis": redis})
    second = await tasks.crawl_dispatch({"redis": redis})

    assert second == {"enqueued": 0, "duplicated": 10}
    assert len(redis.calls) == 10


async def test_dispatch_passes_skip_seen_days() -> None:
    redis = FakeRedis()
    await tasks.crawl_dispatch({"redis": redis})

    for _, args, _ in redis.calls:
        site, _keyword, pages, skip_seen_days = args
        assert skip_seen_days == 7
        assert pages == CRAWL_CONFIG[site]["pages"]


class _Stats:
    def __init__(self, changed: list[int]) -> None:
        self.changed_posting_ids = changed
        self.fetched = len(changed)
        self.inserted = len(changed)
        self.updated = 0
        self.skipped = 0
        self.skipped_known = 0
        self.errors = 0

    def as_line(self) -> str:
        return "stub"


class _StubService:
    stats = _Stats([])

    def __init__(self, crawler: Any) -> None:
        pass

    async def crawl(self, **_: Any):
        return type(self).stats


class _StubCrawlerCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def _patch_crawl(monkeypatch):
    monkeypatch.setattr(tasks, "build_crawler", lambda *a, **k: _StubCrawlerCtx())
    monkeypatch.setattr(tasks, "CrawlService", _StubService)


@pytest.fixture(autouse=True)
def _settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_crawl_site_does_not_enqueue_embedding(_patch_crawl) -> None:
    _StubService.stats = _Stats([11, 22, 33])
    redis = FakeRedis()

    await tasks.crawl_site({"redis": redis}, "jumpit", None, 1, 7)

    assert redis.calls == []
