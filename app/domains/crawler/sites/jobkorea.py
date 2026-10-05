# 잡코리아 목록과 상세 수집 (HTML)

import copy
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

from bs4 import BeautifulSoup, Tag

from app.domains.crawler import extractor
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites import htmlutil as hu
from app.domains.crawler.sites.base import BaseSiteCrawler, CrawlError, ParseError

log = logging.getLogger(__name__)


def _job_id(href: Optional[str]) -> Optional[str]:
    match = re.search(r"/Recruit/GI_Read/(\d+)", href or "")
    return match.group(1) if match else None


def _parse_date(value: Any) -> Optional[datetime]:
    text = hu.jsonld_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone(timedelta(hours=9))) if parsed.tzinfo is None else parsed


def salary_text_from_jsonld(base_salary: Any) -> Optional[str]:
    if not isinstance(base_salary, dict):
        return None
    value = base_salary.get("value")
    amount = value.get("value") if isinstance(value, dict) else value
    unit = (value.get("unitText") if isinstance(value, dict) else None) or "YEAR"

    if not isinstance(amount, (int, float)) or amount <= 0:
        return None
    if (base_salary.get("currency") or "KRW").upper() != "KRW":
        return None

    man_won = int(amount) // 10_000
    if man_won <= 0:
        return None
    unit_label = {"YEAR": "", "MONTH": "월 ", "HOUR": "시급 "}
    return f"{unit_label.get(unit.upper(), '')}{man_won:,}만원"


class JobkoreaCrawler(BaseSiteCrawler):
    source = "jobkorea"

    BASE = "https://www.jobkorea.co.kr"
    LIST_URL = f"{BASE}/Search/"
    DETAIL_URL = f"{BASE}/Recruit/GI_Read/{{job_id}}"
    SELECTORS = {
        "job_link": "a[href*='/Recruit/GI_Read/']",
        "body_fallback": "#tbCont, .tbCont, .detailArea, .view-content",
    }
    _EMPLOYMENT = {
        "FULL_TIME": "정규직",
        "PART_TIME": "파트타임",
        "CONTRACTOR": "계약직",
        "TEMPORARY": "계약직",
        "INTERN": "인턴",
    }

    # 봇 탐지가 있어 딜레이 2.5초, 동시성 1 을 유지한다
    DELAY_SECONDS = 2.5
    concurrency = 1

    referer = f"{BASE}/"

    def __init__(self, *, keyword: str = "백엔드", **kwargs: Any) -> None:
        kwargs.setdefault("delay", self.DELAY_SECONDS)
        super().__init__(**kwargs)
        self.keyword = keyword

    def default_headers(self) -> dict[str, str]:
        headers = super().default_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        headers["Upgrade-Insecure-Requests"] = "1"
        return headers

    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        soup = await self.get_soup(
            self.LIST_URL,
            params={"stext": self.keyword, "tabType": "recruit", "Page_No": page},
            snapshot=f"list_p{page}",
        )
        return self.parse_list(soup, page)

    def parse_list(self, soup: BeautifulSoup, page: int) -> tuple[list[RawJob], int]:
        links = soup.select(self.SELECTORS["job_link"])
        if not links:
            raise ParseError(
                f"잡코리아 목록에서 공고 링크를 찾지 못했습니다 (page={page}). "
                "차단되었거나 사이트가 개편됐습니다. data/raw/jobkorea/ 스냅샷을 확인하세요."
            )

        jobs: list[RawJob] = []
        seen: set[str] = set()
        for link in links:
            job_id = _job_id(link.get("href"))
            if not job_id or job_id in seen:
                continue
            title = link.get_text(" ", strip=True)
            if not title:
                continue
            seen.add(job_id)
            jobs.append(
                RawJob(
                    source=self.source,
                    source_job_id=job_id,
                    url=self.DETAIL_URL.format(job_id=job_id),
                    title=title,
                    company_name=self._company_near(link),
                )
            )
        return jobs, 0

    @staticmethod
    def _company_near(link: Tag) -> str:
        block: Optional[Tag] = link
        for _ in range(5):
            if block is None or block.parent is None:
                break
            block = block.parent
            corp = block.select_one("a[href*='/Recruit/Co_Read/'], a[href*='/company/']")
            if corp and (name := corp.get_text(" ", strip=True)):
                return name[:200]
        return ""

    async def fetch_detail(self, job: RawJob) -> RawJob:
        try:
            soup = await self.get_soup(
                self.DETAIL_URL.format(job_id=job.source_job_id),
                snapshot=f"position_{job.source_job_id}",
            )
        except CrawlError as exc:
            log.warning("잡코리아 상세 실패 id=%s: %s", job.source_job_id, exc)
            return job
        return self.merge_detail(job, soup)

    def merge_detail(self, job: RawJob, soup: BeautifulSoup) -> RawJob:
        update: dict[str, Any] = {"detail_fetched": True}

        posting = hu.jobposting_from_jsonld(soup)
        if posting is None:
            log.warning("잡코리아 JSON-LD 없음 id=%s — 본문만 수집합니다", job.source_job_id)
        else:
            org = posting.get("hiringOrganization") or {}
            location = posting.get("jobLocation") or {}
            address = location.get("address") if isinstance(location, dict) else None
            employment = hu.jsonld_text(posting.get("employmentType"))

            career_min, career_max = hu.parse_career(
                hu.jsonld_text(posting.get("experienceRequirements"))
            )
            update |= {
                "title": hu.jsonld_text(posting.get("title")) or job.title,
                "company_name": hu.jsonld_text(org) or job.company_name,
                "education": hu.jsonld_text(posting.get("educationRequirements")),
                "employment_type": self._EMPLOYMENT.get(employment or "", employment),
                "career_min": career_min,
                "career_max": career_max,
                "newcomer": career_min == 0,
                "published_at": _parse_date(posting.get("datePosted")),
                "closed_at": _parse_date(posting.get("validThrough")) or job.closed_at,
                "salary_raw": salary_text_from_jsonld(posting.get("baseSalary")),
                "locations": [
                    text for text in [hu.jsonld_text(address) if address else None] if text
                ],
            }

        # JSON-LD description 은 SEO 용 자동 문장이라 본문은 HTML 에서 따로 뽑는다
        update |= self.extract_body(soup)

        clean = {k: v for k, v in update.items() if v not in (None, "", [])}
        clean["detail_fetched"] = True
        clean["body_is_image"] = update.get("body_is_image", False)
        clean["body_extract_failed"] = update.get("body_extract_failed", True)
        return job.merged(clean)

    @staticmethod
    def extract_body(soup: BeautifulSoup) -> dict[str, Any]:
        soup = hu.strip_boilerplate(copy.copy(soup))

        node = soup.select_one(JobkoreaCrawler.SELECTORS["body_fallback"])
        if node is None or not hu.block_text(node):
            node = hu.section_anchored_block(soup, extractor.section_header_pattern())
        if node is None:
            node = hu.largest_text_block(soup)

        text = hu.block_text(node)
        is_image, failed, images = hu.classify_body(
            node, text, is_valid=extractor.is_valid_body(text)
        )
        if failed:
            log.debug("잡코리아 본문 추출 실패 (길이 %d)", len(text))
        return {
            "responsibility": text or None,
            "body_is_image": is_image,
            "body_extract_failed": failed,
            "image_urls": images,
        }
