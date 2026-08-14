"""arq 태스크 — 큐잉 규칙만 본다. 실제 워커를 띄우지 않는다.

여기서 못 박는 것
    - crawl_dispatch 가 CRAWL_CONFIG 대로 10개를 팬아웃한다 (잡코리아 제외)
    - _job_id 형식이 f"crawl:{site}:{keyword}:{date}" 다 (중복 큐잉 차단)
    - 같은 날 두 번 돌리면 두 번째는 전부 중복으로 잡힌다
      → 워커 재시작(run_at_startup)이 같은 날 수집을 다시 돌리지 않는다
    - crawl_site 는 **변경분만** embed_postings 로 넘기고,
      EMBED_ENQUEUE_CHUNK 개씩 쪼갠다

redis 는 호출을 기록하는 스텁이다. arq 는 이미 있는 _job_id 에 대해
enqueue_job 이 None 을 돌려주므로 그 동작을 그대로 흉내 낸다.
"""

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
    """arq 의 enqueue_job 계약만 흉내 낸다."""

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


# ── 설정 ────────────────────────────────────────────────────────────────────
def test_config_expands_to_10_jobs() -> None:
    jobs = iter_crawl_jobs()
    assert len(jobs) == 10

    by_site: dict[str, int] = {}
    for job in jobs:
        by_site[job.site] = by_site.get(job.site, 0) + 1

    # 점핏·원티드는 개발 직군 전용이라 키워드 없이 전체 순회 → 1개씩
    assert by_site == {"jumpit": 1, "wanted": 1, "saramin": 8}
    assert len(KEYWORDS) == 8


def test_jobkorea_is_out_of_the_batch_but_still_buildable() -> None:
    """스킬 수율 15% 라 배치에서 뺐다. 어댑터는 재파싱·수동 실행용으로 남긴다."""
    assert "jobkorea" not in CRAWL_CONFIG
    assert all(job.site != "jobkorea" for job in iter_crawl_jobs())
    assert "jobkorea" in SITE_CLASSES


def test_keywordless_sites_get_no_keyword() -> None:
    jobs = {j.site: j for j in iter_crawl_jobs() if j.site in {"jumpit", "wanted"}}
    assert jobs["jumpit"].keyword is None
    assert jobs["wanted"].keyword is None
    assert jobs["jumpit"].pages == CRAWL_CONFIG["jumpit"]["pages"]


# ── crawl_dispatch ──────────────────────────────────────────────────────────
async def test_dispatch_fans_out_with_dedup_job_ids() -> None:
    redis = FakeRedis()

    result = await tasks.crawl_dispatch({"redis": redis})

    assert result == {"enqueued": 10, "duplicated": 0}
    assert {name for name, _, _ in redis.calls} == {"crawl_site"}

    job_ids = [job_id for _, _, job_id in redis.calls]
    assert len(set(job_ids)) == 10
    assert all(job_id.startswith("crawl:") for job_id in job_ids)
    # 날짜가 들어가야 다음날 같은 조합이 다시 돈다
    assert all(job_id.split(":")[-1].isdigit() for job_id in job_ids)
    assert "crawl:jumpit:all:" in next(j for j in job_ids if j.startswith("crawl:jumpit"))


async def test_dispatch_twice_is_blocked_by_job_id() -> None:
    redis = FakeRedis()

    await tasks.crawl_dispatch({"redis": redis})
    second = await tasks.crawl_dispatch({"redis": redis})

    assert second == {"enqueued": 0, "duplicated": 10}
    assert len(redis.calls) == 10  # 두 번째는 큐에 아무것도 안 넣었다


async def test_dispatch_passes_skip_seen_days() -> None:
    """★ 이 인자를 빠뜨리면 매일 전량 재수집한다."""
    redis = FakeRedis()
    await tasks.crawl_dispatch({"redis": redis})

    for _, args, _ in redis.calls:
        site, _keyword, pages, skip_seen_days = args
        assert skip_seen_days == 7
        assert pages == CRAWL_CONFIG[site]["pages"]


# ── crawl_site → embed_postings ─────────────────────────────────────────────
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
    """get_settings 는 lru_cache 다. 환경변수를 바꾼 테스트가 뒤 테스트로
    새지 않게 앞뒤로 비운다."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_crawl_site_enqueues_only_changed_postings(_patch_crawl) -> None:
    _StubService.stats = _Stats([11, 22, 33])
    redis = FakeRedis()

    await tasks.crawl_site({"redis": redis}, "jumpit", None, 1, 7)

    assert redis.calls == [("embed_postings", ([11, 22, 33],), None)]


async def test_crawl_site_splits_large_enqueues(_patch_crawl, monkeypatch) -> None:
    """★ 처리량이 낮은 제공자에서는 한 job 에 300건을 넣으면 job_timeout 에 잘린다.
    (Voyage 무료 등급이 분당 12건이었다)

    쪼개 두면 각 job 이 시간 안에 끝나고, 하나가 실패해도 그 조각만 다시 돈다.

    ★ 값을 여기서 고정한다. gemini 로 옮기며 기본값이 0(쪼개지 않음)이 됐지만,
      이 테스트가 확인하는 것은 기본값이 아니라 쪼개는 로직 자체다.
    """
    monkeypatch.setenv("EMBED_ENQUEUE_CHUNK", "40")
    get_settings.cache_clear()

    _StubService.stats = _Stats(list(range(1, 101)))
    redis = FakeRedis()

    await tasks.crawl_site({"redis": redis}, "saramin", "백엔드", 8, 7)

    batches = [args[0] for name, args, _ in redis.calls if name == "embed_postings"]
    assert [len(b) for b in batches] == [40, 40, 20]
    # 쪼개도 하나도 빠지면 안 된다
    assert [pid for batch in batches for pid in batch] == list(range(1, 101))


def test_enqueue_chunk_zero_means_one_job() -> None:
    """표준 등급으로 올리면 EMBED_ENQUEUE_CHUNK=0 으로 두어 한 번에 넘긴다."""
    assert tasks._chunked([1, 2, 3], 0) == [[1, 2, 3]]
    assert tasks._chunked([1, 2, 3], 10) == [[1, 2, 3]]
    assert tasks._chunked([1, 2, 3], 2) == [[1, 2], [3]]


async def test_crawl_site_skips_enqueue_when_nothing_changed(_patch_crawl) -> None:
    """해시가 같아 touch 만 한 공고는 재임베딩할 것이 없다."""
    _StubService.stats = _Stats([])
    redis = FakeRedis()

    await tasks.crawl_site({"redis": redis}, "jumpit", None, 1, 7)

    assert redis.calls == []
