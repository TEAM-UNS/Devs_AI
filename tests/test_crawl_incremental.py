# 증분 수집과 종료 조건 테스트

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.database import get_worker_session
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.service import CrawlService
from app.domains.crawler.sites.base import BaseSiteCrawler, SelectorBrokenError

PER_PAGE = 5


class StubCrawler(BaseSiteCrawler):
    source = "saramin"  # CHECK 제약 때문에 실제 사이트 값이어야 한다
    referer = "https://example.test/"

    def __init__(self, *, tag: str, pages: int, snapshot_dir: Path, empty_first: bool = False):
        super().__init__(snapshot_dir=snapshot_dir)
        self.tag = tag
        self.pages = pages
        self.empty_first = empty_first
        self.detail_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _job(self, page: int, index: int) -> RawJob:
        return RawJob(
            source=self.source,
            source_job_id=f"stub-{self.tag}-{page}-{index}",
            url=f"https://example.test/{page}/{index}",
            title=f"백엔드 개발자 {page}-{index}",
            company_name="",  # 기업 테이블을 건드리지 않는다
        )

    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        if self.empty_first or page > self.pages:
            return [], 0
        return [self._job(page, i) for i in range(PER_PAGE)], self.pages * PER_PAGE

    async def fetch_detail(self, job: RawJob) -> RawJob:
        self.detail_calls.append(job.source_job_id)
        return job.model_copy(
            update={
                "detail_fetched": True,
                "responsibility": "- 결제 서버 API 개발",
                "qualifications": "- Python 3년 이상",
            }
        )


@pytest.fixture
async def tag(db) -> AsyncIterator[str]:
    value = uuid.uuid4().hex[:12]
    yield value

    async with get_worker_session() as session:
        await session.exec(
            text("DELETE FROM market.job_posting WHERE source_job_id LIKE :pattern").bindparams(
                pattern=f"stub-{value}-%"
            )
        )
        await session.exec(
            text("DELETE FROM market.crawl_run WHERE keyword = :kw").bindparams(kw=f"test-{value}")
        )


async def _last_run(keyword: str) -> tuple[str, int, int, int]:
    async with get_worker_session() as session:
        row = (
            await session.exec(
                text(
                    "SELECT status, fetched, inserted, skipped FROM market.crawl_run "
                    "WHERE keyword = :kw ORDER BY started_at DESC LIMIT 1"
                ).bindparams(kw=keyword)
            )
        ).one()
    return row[0], row[1], row[2], row[3]


async def test_second_run_skips_at_list_stage(tag, tmp_path) -> None:
    keyword = f"test-{tag}"

    first = StubCrawler(tag=tag, pages=2, snapshot_dir=tmp_path)
    async with first:
        stats1 = await CrawlService(first).crawl(pages=2, skip_seen_days=7, keyword=keyword)

    assert stats1.inserted == 2 * PER_PAGE
    assert stats1.skipped_known == 0
    assert len(first.detail_calls) == 2 * PER_PAGE

    second = StubCrawler(tag=tag, pages=2, snapshot_dir=tmp_path)
    async with second:
        stats2 = await CrawlService(second).crawl(pages=2, skip_seen_days=7, keyword=keyword)

    assert second.detail_calls == [], "기수집 공고에 상세를 다시 요청했다"
    assert stats2.skipped_known == 2 * PER_PAGE
    assert stats2.inserted == 0
    assert stats2.updated == 0

    status, _, _, skipped = await _last_run(keyword)
    assert status == "success"
    assert skipped == 2 * PER_PAGE


async def test_skip_seen_days_zero_disables_the_filter(tag, tmp_path) -> None:
    keyword = f"test-{tag}"

    first = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with first:
        await CrawlService(first).crawl(pages=1, skip_seen_days=7, keyword=keyword)

    second = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with second:
        stats = await CrawlService(second).crawl(pages=1, skip_seen_days=0, keyword=keyword)

    assert len(second.detail_calls) == PER_PAGE
    assert stats.skipped_known == 0
    assert stats.skipped == PER_PAGE


async def test_force_reextract_rewrites_unchanged_postings(tag, tmp_path) -> None:
    keyword = f"test-{tag}"

    first = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with first:
        await CrawlService(first).crawl(pages=1, skip_seen_days=7, keyword=keyword)

    plain = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with plain:
        stats = await CrawlService(plain).crawl(pages=1, skip_seen_days=0, keyword=keyword)
    assert stats.skipped == PER_PAGE
    assert stats.reextracted == 0

    forced = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with forced:
        stats = await CrawlService(forced, force_reextract=True).crawl(
            pages=1, skip_seen_days=0, keyword=keyword
        )

    assert stats.reextracted == PER_PAGE
    assert stats.skipped == 0
    # 본문은 그대로라 재임베딩 대상에 넣지 않는다
    assert stats.changed_posting_ids == []


async def test_empty_first_page_fails_the_run(tag, tmp_path) -> None:
    keyword = f"test-{tag}"
    crawler = StubCrawler(tag=tag, pages=2, snapshot_dir=tmp_path, empty_first=True)

    with pytest.raises(SelectorBrokenError):
        async with crawler:
            await CrawlService(crawler).crawl(pages=2, skip_seen_days=7, keyword=keyword)

    status, fetched, _, _ = await _last_run(keyword)
    assert status == "failed"
    assert fetched == 0


async def test_exhausted_results_end_normally(tag, tmp_path) -> None:
    keyword = f"test-{tag}"
    crawler = StubCrawler(tag=tag, pages=2, snapshot_dir=tmp_path)

    async with crawler:
        stats = await CrawlService(crawler).crawl(pages=10, skip_seen_days=7, keyword=keyword)

    assert stats.pages == 3
    assert stats.inserted == 2 * PER_PAGE

    status, _, _, _ = await _last_run(keyword)
    assert status == "success"


# ═══════════════════════════════════════════════════════════════════════════
#  중단 조건 — 이미 가진 공고만 연속으로 나오면 멈춘다
# ═══════════════════════════════════════════════════════════════════════════
async def test_stops_after_consecutive_known_pages(tag, tmp_path) -> None:
    """목록이 최신순이므로 기수집 페이지가 연속으로 나오면 그 뒤는 볼 필요가 없다."""
    keyword = f"test-{tag}"

    # 1회차: 10페이지를 전부 받아 둔다
    first = StubCrawler(tag=tag, pages=10, snapshot_dir=tmp_path)
    async with first:
        await CrawlService(first).crawl(pages=10, skip_seen_days=7, keyword=keyword)

    # 2회차: 상한을 10으로 줘도 STOP_AFTER_KNOWN_PAGES(2) 에서 멈춰야 한다
    second = StubCrawler(tag=tag, pages=10, snapshot_dir=tmp_path)
    async with second:
        stats = await CrawlService(second).crawl(pages=10, skip_seen_days=7, keyword=keyword)

    assert stats.pages == CrawlService.STOP_AFTER_KNOWN_PAGES, (
        f"기수집 페이지 {CrawlService.STOP_AFTER_KNOWN_PAGES}번 연속이면 멈춰야 하는데 "
        f"{stats.pages}페이지를 받았다"
    )
    assert second.detail_calls == []


async def test_new_posting_resets_the_known_page_counter(tag, tmp_path) -> None:
    """중간에 신규가 섞이면 카운터가 풀려 더 내려간다 (수집이 밀린 상황)."""
    keyword = f"test-{tag}"

    first = StubCrawler(tag=tag, pages=5, snapshot_dir=tmp_path)
    async with first:
        await CrawlService(first).crawl(pages=5, skip_seen_days=7, keyword=keyword)

    class _MixedCrawler(StubCrawler):
        async def fetch_list_page(self, page: int):
            jobs, total = await super().fetch_list_page(page)
            if page == 2 and jobs:  # 2페이지에 처음 보는 공고를 섞는다
                jobs[0] = jobs[0].model_copy(
                    update={"source_job_id": f"stub-{self.tag}-new-{page}"}
                )
            return jobs, total

    second = _MixedCrawler(tag=tag, pages=5, snapshot_dir=tmp_path)
    async with second:
        stats = await CrawlService(second).crawl(pages=5, skip_seen_days=7, keyword=keyword)

    # p1 기수집(카운터 1) → p2 에 신규가 있어 카운터 0 으로 리셋 →
    # p3·p4 가 다시 기수집이라 2연속으로 p4 에서 멈춘다 (p5 는 안 받는다)
    assert stats.pages == 4
    assert stats.inserted == 1
