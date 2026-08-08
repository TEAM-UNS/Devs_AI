"""증분 수집(skip_seen_days) 과 종료 조건(1페이지 0건) 검증.

네트워크를 타지 않는다. BaseSiteCrawler 를 상속한 스텁이 목록·상세를
직접 돌려주고, 상세 호출 횟수를 센다 — "목록 단계에서 걸렀는가" 는 결국
**fetch_detail 이 안 불렸는가** 로만 증명된다.

실 postgres 를 쓴다(crawl_run · job_posting 기록 확인). 접속이 안 되면
conftest 의 db 픽스처가 skip 한다.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.database import session_scope
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.service import CrawlService
from app.domains.crawler.sites.base import BaseSiteCrawler, SelectorBrokenError

PER_PAGE = 5


class StubCrawler(BaseSiteCrawler):
    """목록·상세를 메모리에서 돌려주는 스텁. HTTP 를 열지 않는다."""

    source = "saramin"  # CHECK 제약이 있는 enum 값이어야 한다
    referer = "https://example.test/"

    def __init__(self, *, tag: str, pages: int, snapshot_dir: Path, empty_first: bool = False):
        super().__init__(snapshot_dir=snapshot_dir)
        self.tag = tag
        self.pages = pages
        self.empty_first = empty_first
        self.detail_calls: list[str] = []

    async def __aenter__(self):  # 클라이언트를 만들지 않는다
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
    """이 테스트가 만든 행만 골라 지우기 위한 표식."""
    value = uuid.uuid4().hex[:12]
    yield value

    async with session_scope() as session:
        await session.exec(
            text("DELETE FROM market.job_posting WHERE source_job_id LIKE :pattern").bindparams(
                pattern=f"stub-{value}-%"
            )
        )
        await session.exec(
            text("DELETE FROM market.crawl_run WHERE keyword = :kw").bindparams(kw=f"test-{value}")
        )


async def _last_run(keyword: str) -> tuple[str, int, int, int]:
    """(status, fetched, inserted, skipped) — crawl_run 마감 상태."""
    async with session_scope() as session:
        row = (
            await session.exec(
                text(
                    "SELECT status, fetched, inserted, skipped FROM market.crawl_run "
                    "WHERE keyword = :kw ORDER BY started_at DESC LIMIT 1"
                ).bindparams(kw=keyword)
            )
        ).one()
    return row[0], row[1], row[2], row[3]


# ═══════════════════════════════════════════════════════════════════════════
#  증분 수집 (명세 3-2)
# ═══════════════════════════════════════════════════════════════════════════
async def test_second_run_skips_at_list_stage(tag, tmp_path) -> None:
    """★ 두 번째 실행은 상세 요청을 하지 않는다.

    상세를 받은 뒤 content_hash 를 비교하면 요청이 이미 나가 있어 절감
    효과가 0이다. 그래서 fetch_detail 호출 횟수로 검증한다.
    """
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
    """0 이면 전량 재수집한다. 파서를 고친 뒤 다시 돌릴 때 쓴다."""
    keyword = f"test-{tag}"

    first = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with first:
        await CrawlService(first).crawl(pages=1, skip_seen_days=7, keyword=keyword)

    second = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with second:
        stats = await CrawlService(second).crawl(pages=1, skip_seen_days=0, keyword=keyword)

    assert len(second.detail_calls) == PER_PAGE
    assert stats.skipped_known == 0
    # 내용이 그대로라 content_hash 가 같다 → skipped(해시 동일) 로 잡힌다.
    assert stats.skipped == PER_PAGE


# ═══════════════════════════════════════════════════════════════════════════
#  파서 수정 후 백필 (force_reextract)
# ═══════════════════════════════════════════════════════════════════════════
async def test_force_reextract_rewrites_unchanged_postings(tag, tmp_path) -> None:
    """★ content_hash 는 사이트 원문으로만 계산한다 — 파서가 바뀐 걸 모른다.

    employment_type 은 해시에 아예 없어서, 파서를 고치고 재수집해도 해시가
    같으면 touch 만 하고 지나간다. 그러면 NULL 이 그대로 남는다.
    """
    keyword = f"test-{tag}"

    first = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with first:
        await CrawlService(first).crawl(pages=1, skip_seen_days=7, keyword=keyword)

    # 플래그 없이: 해시가 같으니 그냥 넘어간다
    plain = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with plain:
        stats = await CrawlService(plain).crawl(pages=1, skip_seen_days=0, keyword=keyword)
    assert stats.skipped == PER_PAGE
    assert stats.reextracted == 0

    # 플래그 켜면: 해시가 같아도 다시 적재한다
    forced = StubCrawler(tag=tag, pages=1, snapshot_dir=tmp_path)
    async with forced:
        stats = await CrawlService(forced, force_reextract=True).crawl(
            pages=1, skip_seen_days=0, keyword=keyword
        )

    assert stats.reextracted == PER_PAGE
    assert stats.skipped == 0
    # ★ 본문은 그대로다. 재임베딩 큐에 넣으면 사람인 전체가 임베딩으로 들어간다.
    assert stats.changed_posting_ids == []


# ═══════════════════════════════════════════════════════════════════════════
#  종료 조건 (명세 3-3)
# ═══════════════════════════════════════════════════════════════════════════
async def test_empty_first_page_fails_the_run(tag, tmp_path) -> None:
    """★ 1페이지 0건은 '결과 없음' 이 아니라 '셀렉터가 깨졌다' 다.

    조용히 break 하면 매일 0건을 수집하면서 crawl_run 은 success 로 남는다.
    """
    keyword = f"test-{tag}"
    crawler = StubCrawler(tag=tag, pages=2, snapshot_dir=tmp_path, empty_first=True)

    with pytest.raises(SelectorBrokenError):
        async with crawler:
            await CrawlService(crawler).crawl(pages=2, skip_seen_days=7, keyword=keyword)

    status, fetched, _, _ = await _last_run(keyword)
    assert status == "failed"
    assert fetched == 0


async def test_exhausted_results_end_normally(tag, tmp_path) -> None:
    """2페이지 이후 0건은 결과 소진 — 정상 종료다."""
    keyword = f"test-{tag}"
    crawler = StubCrawler(tag=tag, pages=2, snapshot_dir=tmp_path)

    async with crawler:
        stats = await CrawlService(crawler).crawl(pages=10, skip_seen_days=7, keyword=keyword)

    assert stats.pages == 3  # 1 · 2 페이지 + 빈 3페이지
    assert stats.inserted == 2 * PER_PAGE

    status, _, _, _ = await _last_run(keyword)
    assert status == "success"
