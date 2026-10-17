# 사람인 기업정보 페이지에서 회사 로고 URL 만 받는다

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional, Union

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.crawler import enums, repository
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites.base import BaseSiteCrawler, CrawlError

log = logging.getLogger(__name__)

Factory = Union[Callable[[], AsyncSession], async_sessionmaker[AsyncSession]]


@dataclass
class LogoStats:
    targets: int = 0
    fetched: int = 0
    saved: int = 0
    missing: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)

    def as_line(self) -> str:
        return (
            f"대상 {self.targets} · 요청 {self.fetched} · 저장 {self.saved} · "
            f"로고없음 {self.missing} · 오류 {self.errors}"
        )


class SaraminCompanyCrawler(BaseSiteCrawler):
    source = enums.CrawlSource.SARAMIN.value
    referer = "https://www.saramin.co.kr/"
    concurrency = 1

    VIEW_URL = "https://www.saramin.co.kr/zf_user/company-info/view?csn={csn}"
    LOGO_HOST = "https://clogo.saramin.co.kr/"

    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        raise NotImplementedError("회사 로고 전용 크롤러입니다")

    async def fetch_detail(self, job: RawJob) -> RawJob:
        raise NotImplementedError("회사 로고 전용 크롤러입니다")

    async def fetch_logo(self, csn: str) -> Optional[str]:
        try:
            soup = await self.get_soup(self.VIEW_URL.format(csn=csn))
        except httpx.HTTPStatusError as exc:
            # 폐업이나 삭제로 없어진 회사. 다시 받아도 404 라 로고 없음으로 둔다
            if exc.response.status_code == 404:
                return None
            raise
        return self.parse_logo(soup)

    def parse_logo(self, soup: BeautifulSoup) -> Optional[str]:
        meta = soup.select_one('meta[property="og:image"]')
        url = (meta.get("content") or "").strip() if meta else ""
        if not url:
            img = soup.find("img", alt=re.compile("기업로고"))
            url = (img.get("src") or "").strip() if img else ""

        # 로고가 없으면 기본 공유 이미지가 온다. 그걸 받으면 전 회사가 같은 그림이 된다
        return url if url.startswith(self.LOGO_HOST) else None


async def collect_logos(
    factory: Factory,
    *,
    limit: Optional[int] = None,
    retry_after_days: int = 30,
) -> LogoStats:
    stats = LogoStats()

    async with factory() as session:
        targets = await repository.companies_missing_logo(
            session,
            source=enums.CrawlSource.SARAMIN,
            limit=limit,
            retry_after_days=retry_after_days,
        )

    stats.targets = len(targets)
    if not targets:
        return stats

    async with SaraminCompanyCrawler() as crawler:
        for company_id, csn in targets:
            # 한 회사가 실패해도 전체를 멈추지 않는다. 도장을 찍지 않으므로 다음 실행에 다시 본다
            try:
                logo_url = await crawler.fetch_logo(csn)
            except (CrawlError, httpx.HTTPStatusError) as exc:
                stats.errors += 1
                if len(stats.error_messages) < 5:
                    stats.error_messages.append(f"company_id={company_id}: {exc}")
                continue

            stats.fetched += 1
            async with factory() as session:
                await repository.save_company_logo(
                    session, company_id=company_id, logo_url=logo_url
                )
                await session.commit()

            if logo_url:
                stats.saved += 1
            else:
                stats.missing += 1

    log.info("회사 로고 수집 — %s", stats.as_line())
    return stats
