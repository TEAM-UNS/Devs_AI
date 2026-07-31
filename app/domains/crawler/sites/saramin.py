"""사람인 — 서버 렌더 HTML 파싱.

실제 응답을 확인하고 구조를 고정했다 (2026-07-30 기준).

    목록  GET /zf_user/search?searchType=search&searchword=..&recruitPage=N
          .item_recruit 블록. 여기서 rec_idx · 제목 · 회사명 · 직무키워드를 얻는다.

    조건  GET /zf_user/jobs/relay/view-ajax?rec_idx=N
          ★ 이게 핵심이다. 상세 페이지(/jobs/relay/view)의 조건 영역은
          JS 로 렌더링돼서 httpx 로 받으면 비어 있다. 그 영역을 채우는
          ajax 를 직접 부르면 경력·학력·급여·근무지·마감일과
          기업정보(사원수·기업형태·업종·설립일·매출·홈페이지)가 전부 나온다.

    본문  GET /zf_user/jobs/relay/view-detail?rec_idx=N&rec_seq=0
          상세 요강은 iframe 안에 있다. iframe src 를 직접 부른다.

3중 폴백
    1순위 JSON-LD  — 사람인 상세에는 JobPosting 이 **없다**(BreadcrumbList 뿐).
                     그래도 코드는 남겨둔다. 언제 생겨도 이득이고 비용이 0이다.
    2순위 라벨-값  — 실질적인 주력. "경력" "사원수" 같은 한글 라벨로 꺼낸다.
    3순위 셀렉터   — 목록 아이템처럼 라벨이 없는 곳에서만. SELECTORS 에 몰아둔다.
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from app.domains.crawler import extractor
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites import htmlutil as hu
from app.domains.crawler.sites.base import BaseSiteCrawler, CrawlError, ParseError

log = logging.getLogger(__name__)

BASE = "https://www.saramin.co.kr"
LIST_URL = f"{BASE}/zf_user/search"
AJAX_URL = f"{BASE}/zf_user/jobs/relay/view-ajax"
BODY_URL = f"{BASE}/zf_user/jobs/relay/view-detail"
VIEW_URL = f"{BASE}/zf_user/jobs/relay/view?rec_idx={{rec_idx}}"

PAGE_SIZE = 40
KST = timezone(timedelta(hours=9))

# ── 3순위 폴백. 개편 시 여기만 고치면 된다. ────────────────────────────────
SELECTORS = {
    "item": ".item_recruit",
    "title_link": ".job_tit a",
    "company": ".corp_name a",
    "sector": ".job_sector",
    "sector_noise": ".job_day",  # "수정일 26/07/21" — 직무 키워드가 아니다
    "deadline": ".job_date .date",
    "body": ".user_content",
}

_REC_IDX = re.compile(r"rec_idx=(\d+)")
_CSN = re.compile(r"[?&]csn=([^&#]+)")
_DEADLINE = re.compile(r"(\d{1,2})/(\d{1,2})")


def _rec_idx(href: str | None) -> str | None:
    match = _REC_IDX.search(href or "")
    return match.group(1) if match else None


def _parse_deadline(text: str | None) -> datetime | None:
    """ "~ 09/20(일)" → 올해 기준 마감일. 연도가 없어서 추정해야 한다.

    이미 지난 월/일이면 내년으로 본다 (12월에 "01/15" 공고가 흔하다).
    """
    if not text:
        return None
    match = _DEADLINE.search(text)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    today = datetime.now(KST)
    year = today.year
    try:
        candidate = datetime(year, month, day, 23, 59, 59, tzinfo=KST)
    except ValueError:
        return None
    if (candidate - today) < timedelta(days=-180):
        candidate = candidate.replace(year=year + 1)
    return candidate


def _parse_datetime(text: str | None) -> datetime | None:
    """ "2026.08.21 23:59" → datetime(KST)."""
    if not text:
        return None
    match = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if not match:
        return None
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    try:
        return datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None


class SaraminCrawler(BaseSiteCrawler):
    source = "saramin"
    referer = f"{BASE}/"
    concurrency = 2

    def __init__(self, *, keyword: str = "백엔드", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.keyword = keyword

    def default_headers(self) -> dict[str, str]:
        headers = super().default_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        # view-ajax 는 XHR 로만 정상 응답한다.
        headers["X-Requested-With"] = "XMLHttpRequest"
        return headers

    # ── 목록 ──────────────────────────────────────────────────────────────
    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        soup = await self.get_soup(
            LIST_URL,
            params={
                "searchType": "search",
                "searchword": self.keyword,
                "recruitPage": page,
                "recruitPageCount": PAGE_SIZE,
            },
            snapshot=f"list_p{page}",
        )
        return self.parse_list(soup, page)

    def parse_list(self, soup: BeautifulSoup, page: int) -> tuple[list[RawJob], int]:
        items = soup.select(SELECTORS["item"])
        if not items:
            # 검색 결과가 0건인 게 아니라 셀렉터가 깨진 것으로 본다.
            # 사람인 IT 검색이 진짜로 0건일 수는 없다.
            raise ParseError(
                f"사람인 목록에서 {SELECTORS['item']} 를 찾지 못했습니다 (page={page}). "
                "사이트 개편일 가능성이 큽니다. data/raw/saramin/ 의 스냅샷을 확인하세요."
            )

        jobs: list[RawJob] = []
        seen: set[str] = set()
        for item in items:
            try:
                job = self._map_list_item(item)
            except (AttributeError, TypeError, ValueError) as exc:
                log.warning("사람인 목록 항목 매핑 실패: %s", exc)
                continue
            if job and job.source_job_id not in seen:
                seen.add(job.source_job_id)
                jobs.append(job)

        total = self._parse_total(soup)
        return jobs, total

    def _parse_total(self, soup: BeautifulSoup) -> int:
        """ "총 2,671건" 에서 전체 건수. 못 찾으면 0 (진행에 지장 없음)."""
        text = soup.title.get_text() if soup.title else ""
        match = re.search(r"총\s*([\d,]+)\s*건", text)
        return int(match.group(1).replace(",", "")) if match else 0

    def _map_list_item(self, item) -> RawJob | None:
        link = item.select_one(SELECTORS["title_link"]) or item.select_one("a[href*='rec_idx=']")
        rec_idx = _rec_idx(link.get("href") if link else None)
        if not rec_idx:
            return None

        # title 속성이 본문 텍스트보다 깨끗하다 (줄바꿈·하이라이트 태그 없음)
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        company_node = item.select_one(SELECTORS["company"])
        company = company_node.get_text(" ", strip=True) if company_node else ""

        # 직무 키워드. "수정일 …" 은 같은 블록에 있지만 키워드가 아니라 떼어낸다.
        sector = item.select_one(SELECTORS["sector"])
        keywords: list[str] = []
        if sector:
            noise = sector.select_one(SELECTORS["sector_noise"])
            if noise:
                noise.extract()
            keywords = [
                part.strip()
                for part in sector.get_text(",", strip=True).split(",")
                if part.strip() and part.strip() != "외"
            ]

        deadline_node = item.select_one(SELECTORS["deadline"])

        return RawJob(
            source=self.source,
            source_job_id=rec_idx,
            url=VIEW_URL.format(rec_idx=rec_idx),
            title=title,
            company_name=company,
            tech_stacks=keywords,
            job_categories=keywords,
            closed_at=_parse_deadline(
                deadline_node.get_text(" ", strip=True) if deadline_node else None
            ),
            raw={"list_condition": hu.block_text(item.select_one(".job_condition"))},
        )

    # ── 상세 ──────────────────────────────────────────────────────────────
    async def fetch_detail(self, job: RawJob) -> RawJob:
        rec_idx = job.source_job_id
        try:
            condition_soup = await self.get_soup(
                AJAX_URL, params={"rec_idx": rec_idx}, snapshot=f"cond_{rec_idx}"
            )
        except CrawlError as exc:
            log.warning("사람인 조건 수집 실패 rec_idx=%s: %s", rec_idx, exc)
            return job

        try:
            body_soup = await self.get_soup(
                BODY_URL,
                params={"rec_idx": rec_idx, "rec_seq": 0},
                snapshot=f"body_{rec_idx}",
            )
        except CrawlError as exc:
            log.warning("사람인 본문 수집 실패 rec_idx=%s: %s", rec_idx, exc)
            body_soup = None

        return self.merge_detail(job, condition_soup, body_soup)

    def merge_detail(
        self, job: RawJob, condition: BeautifulSoup, body: BeautifulSoup | None
    ) -> RawJob:
        update: dict[str, Any] = {"detail_fetched": True}

        # 1순위 — JSON-LD (현재 사람인엔 없지만 생기면 자동으로 쓰인다)
        if posting := hu.jobposting_from_jsonld(condition):
            update |= self._from_jsonld(posting)

        # 2순위 — 라벨-값
        pairs = hu.label_value_pairs(condition)
        career_min, career_max = hu.parse_career(hu.pick(pairs, "career"))
        company_types = [
            part.strip()
            for part in (hu.pick(pairs, "company_type") or "").split(",")
            if part.strip()
        ]

        update |= {
            "career_min": career_min,
            "career_max": career_max,
            "newcomer": career_min == 0,
            "education": hu.pick(pairs, "education"),
            "employment_type": hu.pick(pairs, "employment_type"),
            "salary_raw": hu.pick(pairs, "salary"),
            "locations": [loc for loc in [hu.pick(pairs, "location")] if loc],
            "published_at": _parse_datetime(hu.pick(pairs, "posted_at")),
            "closed_at": _parse_datetime(hu.pick(pairs, "expires_at")) or job.closed_at,
            "company_tags": company_types,
            "company_industry": hu.pick(pairs, "industry"),
            "company_employee_count": hu.parse_employee_count(hu.pick(pairs, "employee")),
            "company_revenue": hu.parse_revenue(hu.pick(pairs, "revenue")),
            "company_establish_date": hu.pick(pairs, "founded"),
            "company_url": hu.pick(pairs, "homepage"),
        }

        # 3순위 — 회사명은 라벨이 없다. 셀렉터로만 얻는다.
        company_node = condition.select_one("a[href*='company-info'], .company_nm, .corp_name")
        if company_node and (name := company_node.get_text(" ", strip=True)):
            update["company_name"] = name

        # 사이트 내부 기업 id(csn). name_key 병합이 틀렸을 때 추적할 유일한 근거다.
        if csn := self._company_csn(condition):
            update["company_source_id"] = csn

        # 본문 — iframe 안. br 을 살려야 섹션 헤더가 보존된다.
        if body is not None:
            body = hu.strip_boilerplate(copy.copy(body))
            node = body.select_one(SELECTORS["body"]) or body.body
            text = hu.block_text(node)
            # 길이가 아니라 내용으로 판정한다 (extractor.is_valid_body).
            is_image, failed, images = hu.classify_body(
                node, text, is_valid=extractor.is_valid_body(text)
            )
            update |= {
                "responsibility": text or None,
                "body_is_image": is_image,
                "body_extract_failed": failed,
                "image_urls": images,
            }

        # None 으로 기존 값을 지우지 않는다 (목록에서 얻은 값이 더 나을 수 있다)
        clean = {k: v for k, v in update.items() if v not in (None, "", [])}
        clean["detail_fetched"] = True
        # False 도 의미 있는 값이라 위 필터에서 걸러지지 않게 다시 넣는다.
        clean["body_is_image"] = update.get("body_is_image", False)
        clean["body_extract_failed"] = update.get("body_extract_failed", body is None)
        return job.model_copy(update=clean)

    @staticmethod
    def _company_csn(soup: BeautifulSoup) -> str | None:
        """company-info 링크의 csn(기업 일련번호)을 뽑는다.

        /zf_user/company-info/view?csn=xxxx 형태. csn 이 없는 링크
        (sri-certification?seq=..) 도 있으므로 csn 만 골라낸다.
        """
        for anchor in soup.select("a[href*='company-info']"):
            if match := _CSN.search(anchor.get("href") or ""):
                return match.group(1)
        return None

    @staticmethod
    def _from_jsonld(posting: dict[str, Any]) -> dict[str, Any]:
        """schema.org JobPosting → RawJob 필드."""
        org = posting.get("hiringOrganization") or {}
        return {
            key: value
            for key, value in {
                "title": hu.jsonld_text(posting.get("title")),
                "company_name": hu.jsonld_text(org),
                "employment_type": hu.jsonld_text(posting.get("employmentType")),
                "salary_raw": hu.jsonld_text(posting.get("baseSalary")),
                "responsibility": hu.jsonld_text(posting.get("description")),
            }.items()
            if value
        }


def _now_kst() -> datetime:
    return datetime.now(UTC).astimezone(KST)
