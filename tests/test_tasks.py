# arq 수집 태스크 큐잉 테스트

from __future__ import annotations

from typing import Optional, Any

import pytest

from app.core.config import get_settings
from app.domains.crawler import tasks
from app.domains.crawler.config import build_crawler, iter_crawl_jobs
from app.domains.crawler.sites.jobkorea import JobkoreaCrawler


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], Optional[str]]] = []
        self._taken: set[str] = set()

    async def enqueue_job(self, name: str, *args: Any, _job_id: Optional[str] = None, **_: Any):
        if _job_id is not None and _job_id in self._taken:
            return None  # arq 는 중복 job_id 에 None 을 돌려준다
        if _job_id is not None:
            self._taken.add(_job_id)
        self.calls.append((name, args, _job_id))
        return object()


def test_config_expands_to_10_jobs() -> None:
    jobs = iter_crawl_jobs()
    # 원티드는 등록일을 안 줘서 배치에서 뺐다 (점핏 1 + 사람인 8)
    assert len(jobs) == 9

    by_site: dict[str, int] = {}
    for job in jobs:
        by_site[job.site] = by_site.get(job.site, 0) + 1

    assert by_site == {"jumpit": 1, "saramin": 8}
    assert len({job.keyword for job in jobs if job.site == "saramin"}) == 8


def test_jobkorea_is_out_of_the_batch_but_still_buildable(tmp_path) -> None:
    assert all(job.site != "jobkorea" for job in iter_crawl_jobs())
    assert isinstance(build_crawler("jobkorea", snapshot_dir=tmp_path), JobkoreaCrawler)


def test_keywordless_sites_get_no_keyword() -> None:
    jobs = {j.site: j for j in iter_crawl_jobs() if j.site == "jumpit"}
    assert jobs["jumpit"].keyword is None
    assert "wanted" not in {j.site for j in iter_crawl_jobs()}
    assert jobs["jumpit"].pages == 60


async def test_dispatch_fans_out_with_dedup_job_ids() -> None:
    redis = FakeRedis()

    result = await tasks.crawl_dispatch({"redis": redis})

    assert result == {"enqueued": 9, "duplicated": 0}
    assert {name for name, _, _ in redis.calls} == {"crawl_site"}

    job_ids = [job_id for _, _, job_id in redis.calls]
    assert len(set(job_ids)) == 9
    assert all(job_id.startswith("crawl:") for job_id in job_ids)
    assert all(job_id.split(":")[-1].isdigit() for job_id in job_ids)
    assert "crawl:jumpit:all:" in next(j for j in job_ids if j.startswith("crawl:jumpit"))


async def test_dispatch_twice_is_blocked_by_job_id() -> None:
    redis = FakeRedis()

    await tasks.crawl_dispatch({"redis": redis})
    second = await tasks.crawl_dispatch({"redis": redis})

    assert second == {"enqueued": 0, "duplicated": 9}
    assert len(redis.calls) == 9


async def test_dispatch_passes_skip_seen_days() -> None:
    redis = FakeRedis()
    await tasks.crawl_dispatch({"redis": redis})

    for _, args, _ in redis.calls:
        site, _keyword, pages, skip_seen_days = args
        assert skip_seen_days == 7
        # pages 는 수집 범위가 아니라 안전 상한이다 (중단은 CrawlService 가 판단)
        assert pages == {"jumpit": 60, "saramin": 80}[site]


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


@pytest.fixture
def _record_embed(monkeypatch):
    sources: list[str] = []

    async def fake_run_embed(ctx: Any, *, source: str, runner: Any) -> dict[str, Any]:
        sources.append(source)
        return {"postings": 3, "companies": 1, "chunks_written": 9, "errors": 0}

    monkeypatch.setattr(tasks, "_run_embed", fake_run_embed)
    return sources


async def test_crawl_site_hands_embedding_to_the_queue(_patch_crawl, _record_embed) -> None:
    """★ 같은 작업에서 임베딩까지 하면 arq 제한시간 하나를 둘이 나눠 쓴다.
    수집이 500초를 쓰면 임베딩에 100초만 남아 거기서 죽는다 (실측 2026-10-09:
    수집은 success 인데 청크 없는 공고가 686건 남았다). 따로 큐에 넘겨야 한다.
    """
    _StubService.stats = _Stats([11, 22, 33])
    redis = FakeRedis()

    result = await tasks.crawl_site({"redis": redis}, "jumpit", None, 1, 7)

    assert _record_embed == [], "수집 작업 안에서 임베딩을 돌렸다"
    assert [(name, args) for name, args, _ in redis.calls] == [
        ("embed_postings", ([11, 22, 33],))
    ]
    assert result["embed_queued"] == 3


async def test_crawl_site_without_changes_queues_nothing(_patch_crawl, _record_embed) -> None:
    _StubService.stats = _Stats([])
    redis = FakeRedis()

    result = await tasks.crawl_site({"redis": redis}, "jumpit", None, 1, 7)

    assert _record_embed == []
    assert redis.calls == []
    assert result["embed_queued"] == 0
