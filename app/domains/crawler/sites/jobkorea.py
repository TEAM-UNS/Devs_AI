"""잡코리아 — 서버 렌더 HTML 파싱.

실제 응답을 확인하고 구조를 고정했다 (2026-07-30 기준).

    목록  GET /Search/?stext=..&tabType=recruit&Page_No=N
          공고 링크는 /Recruit/GI_Read/{id}. 프론트가 tailwind 로 재작성돼서
          클래스명이 전부 유틸리티 클래스(w-full, flex ...)다. 의미가 없으므로
          링크를 기준으로 블록을 거슬러 올라가 회사명을 찾는다.

    상세  GET /Recruit/GI_Read/{id}
          ★ JobPosting JSON-LD 가 있다. 1순위 폴백이 그대로 먹힌다.
          title · datePosted · validThrough · employmentType
          · experienceRequirements · educationRequirements
          · hiringOrganization.name · jobLocation.address.streetAddress
          · baseSalary{value, unitText} ← 급여가 숫자로 구조화되어 있다

주의
    - JSON-LD 의 description 은 SEO 용 자동 생성 문장이다
      ("○○에서 정규직 경력 채용을 진행합니다"). 실제 요강이 아니므로
      본문은 HTML 에서 따로 뽑는다. 클래스명을 못 믿으니
      htmlutil.largest_text_block() 으로 가장 큰 텍스트 덩어리를 고른다.
    - 라벨-값(dt/dd)은 0개다. 2순위 폴백은 이 사이트에서 동작하지 않는다.
    - 봇 탐지가 있다. delay 2.5초 · 동시성 1 을 반드시 유지한다.
"""

import copy
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.domains.crawler import extractor
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites import htmlutil as hu
from app.domains.crawler.sites.base import BaseSiteCrawler, CrawlError, ParseError

log = logging.getLogger(__name__)

BASE = "https://www.jobkorea.co.kr"
LIST_URL = f"{BASE}/Search/"
DETAIL_URL = f"{BASE}/Recruit/GI_Read/{{job_id}}"

KST = timezone(timedelta(hours=9))

DELAY_SECONDS = 2.5
CONCURRENCY = 1

SELECTORS = {
    "job_link": "a[href*='/Recruit/GI_Read/']",
    "body_fallback": "#tbCont, .tbCont, .detailArea, .view-content",
}

_JOB_ID = re.compile(r"/Recruit/GI_Read/(\d+)")
_EMPLOYMENT = {
    "FULL_TIME": "정규직",
    "PART_TIME": "파트타임",
    "CONTRACTOR": "계약직",
    "TEMPORARY": "계약직",
    "INTERN": "인턴",
}
_UNIT_LABEL = {"YEAR": "", "MONTH": "월 ", "HOUR": "시급 "}
_WON_PER_MAN = 10_000


def _job_id(href: str | None) -> str | None:
    match = _JOB_ID.search(href or "")
    return match.group(1) if match else None


def _parse_date(value: Any) -> datetime | None:
    text = hu.jsonld_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed


def salary_text_from_jsonld(base_salary: Any) -> str | None:
    if not isinstance(base_salary, dict):
        return None
    value = base_salary.get("value")
    amount = value.get("value") if isinstance(value, dict) else value
    unit = (value.get("unitText") if isinstance(value, dict) else None) or "YEAR"

    if not isinstance(amount, (int, float)) or amount <= 0:
        return None
    if (base_salary.get("currency") or "KRW").upper() != "KRW":
        return None

    man_won = int(amount) // _WON_PER_MAN
    if man_won <= 0:
        return None
    return f"{_UNIT_LABEL.get(unit.upper(), '')}{man_won:,}만원"


class JobkoreaCrawler(BaseSiteCrawler):
    source = "jobkorea"
    referer = f"{BASE}/"
    concurrency = CONCURRENCY

    def __init__(self, *, keyword: str = "백엔드", **kwargs: Any) -> None:
        kwargs.setdefault("delay", DELAY_SECONDS)
        super().__init__(**kwargs)
        self.keyword = keyword

    def default_headers(self) -> dict[str, str]:
        headers = super().default_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        headers["Upgrade-Insecure-Requests"] = "1"
        return headers

    # ── 목록 ──────────────────────────────────────────────────────────────
    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        soup = await self.get_soup(
            LIST_URL,
            params={"stext": self.keyword, "tabType": "recruit", "Page_No": page},
            snapshot=f"list_p{page}",
        )
        return self.parse_list(soup, page)

    def parse_list(self, soup: BeautifulSoup, page: int) -> tuple[list[RawJob], int]:
        links = soup.select(SELECTORS["job_link"])
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
                    url=DETAIL_URL.format(job_id=job_id),
                    title=title,
                    company_name=self._company_near(link),
                )
            )
        return jobs, 0

    @staticmethod
    def _company_near(link: Tag) -> str:
        block: Tag | None = link
        for _ in range(5):
            if block is None or block.parent is None:
                break
            block = block.parent
            corp = block.select_one("a[href*='/Recruit/Co_Read/'], a[href*='/company/']")
            if corp and (name := corp.get_text(" ", strip=True)):
                return name[:200]
        return ""

    # ── 상세 ──────────────────────────────────────────────────────────────
    async def fetch_detail(self, job: RawJob) -> RawJob:
        try:
            soup = await self.get_soup(
                DETAIL_URL.format(job_id=job.source_job_id),
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
                "employment_type": _EMPLOYMENT.get(employment or "", employment),
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

        update |= self.extract_body(soup)

        clean = {k: v for k, v in update.items() if v not in (None, "", [])}
        clean["detail_fetched"] = True
        clean["body_is_image"] = update.get("body_is_image", False)
        clean["body_extract_failed"] = update.get("body_extract_failed", True)
        return job.merged(clean)

    @staticmethod
    def extract_body(soup: BeautifulSoup) -> dict[str, Any]:
        soup = hu.strip_boilerplate(copy.copy(soup))

        node = soup.select_one(SELECTORS["body_fallback"])
        if node is None or not hu.block_text(node):
            node = hu.section_anchored_block(soup, extractor.SECTION_RE)
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
