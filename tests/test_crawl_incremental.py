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
