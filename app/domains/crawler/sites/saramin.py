# 사람인 목록과 상세 수집 (HTML)

import copy
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Optional, Any

from bs4 import BeautifulSoup

from app.domains.crawler import extractor
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites import htmlutil as hu
from app.domains.crawler.sites.base import BaseSiteCrawler, CrawlError, ParseError

log = logging.getLogger(__name__)


def _rec_idx(href: Optional[str]) -> Optional[str]:
    match = re.search(r"rec_idx=(\d+)", href or "")
    return match.group(1) if match else None


def _parse_deadline(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    match = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst)
    year = today.year
    try:
        candidate = datetime(year, month, day, 23, 59, 59, tzinfo=kst)
    except ValueError:
        return None
    if (candidate - today) < timedelta(days=-180):
        candidate = candidate.replace(year=year + 1)
    return candidate


def _parse_list_posted_at(text: Optional[str]) -> Optional[datetime]:
    """목록의 "등록일 26/10/03" 에서 등록일만 뽑는다.

    ★ 같은 자리에 "수정일 26/09/28" 이 오기도 한다. 그건 등록일이 아니므로
      받지 않는다 — 수정일을 등록일로 쓰면 오래된 공고가 최근 것으로 집계된다.
    """
    if not text or "등록일" not in text:
        return None
    match = re.search(r"(\d{2})/(\d{1,2})/(\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return datetime(2000 + year, month, day, tzinfo=timezone(timedelta(hours=9)))
    except ValueError:
        return None


def _parse_datetime(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    match = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if not match:
        return None
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    try:
        return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=9)))
    except ValueError:
        return None


class SaraminCrawler(BaseSiteCrawler):
    source = "saramin"

    BASE = "https://www.saramin.co.kr"
    LIST_URL = f"{BASE}/zf_user/search"
    # 상세 페이지 조건 영역은 JS 렌더링이라 그 영역을 채우는 ajax 를 직접 부른다
    AJAX_URL = f"{BASE}/zf_user/jobs/relay/view-ajax"
    BODY_URL = f"{BASE}/zf_user/jobs/relay/view-detail"
    VIEW_URL = f"{BASE}/zf_user/jobs/relay/view?rec_idx={{rec_idx}}"
    PAGE_SIZE = 40
    SELECTORS = {
        "item": ".item_recruit",
        "title_link": ".job_tit a",
        "company": ".corp_name a",
        "sector": ".job_sector",
        "sector_noise": ".job_day",
        "deadline": ".job_date .date",
        # ★ 등록일은 목록에만 있다. 상세의 "시작일" 은 접수 시작일이라 다르다
        #   (접수 예정 공고는 시작일이 미래다 — 실측 2026-10-23 시작 공고).
        "posted": ".job_day",
        "body": ".user_content",
    }

    referer = f"{BASE}/"
    concurrency = 2

    def __init__(self, *, keyword: str = "백엔드", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.keyword = keyword

    def default_headers(self) -> dict[str, str]:
        headers = super().default_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        headers["X-Requested-With"] = "XMLHttpRequest"
        return headers

    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        soup = await self.get_soup(
            self.LIST_URL,
            params={
                "searchType": "search",
                "searchword": self.keyword,
                "recruitPage": page,
                # ★ 정렬 파라미터는 recruitSort 다 (recruitPage · recruitPageCount 와 같은 규칙).
                #   빼면 기본값이 정확도순이라 등록일과 무관한 순서로 온다. 그러면
                #   "앞쪽 N페이지만 봐도 신규를 다 잡는다" 는 전제가 깨진다.
                #   값은 사이트 정렬 버튼이 쓰는 것: relation · accuracy · reg_dt ·
                #   edit_dt · closing_dt · apply_cnt · employ_cnt
                "recruitSort": "reg_dt",
                "recruitPageCount": self.PAGE_SIZE,
            },
            snapshot=f"list_p{page}",
        )
        return self.parse_list(soup, page)

    def parse_list(self, soup: BeautifulSoup, page: int) -> tuple[list[RawJob], int]:
        items = soup.select(self.SELECTORS["item"])
        if not items:
            raise ParseError(
                f"사람인 목록에서 {self.SELECTORS['item']} 를 찾지 못했습니다 (page={page}). "
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
        text = soup.title.get_text() if soup.title else ""
        match = re.search(r"총\s*([\d,]+)\s*건", text)
        return int(match.group(1).replace(",", "")) if match else 0

    def _map_list_item(self, item) -> Optional[RawJob]:
        link = item.select_one(self.SELECTORS["title_link"]) or item.select_one("a[href*='rec_idx=']")
        rec_idx = _rec_idx(link.get("href") if link else None)
        if not rec_idx:
            return None

        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        company_node = item.select_one(self.SELECTORS["company"])
        company = company_node.get_text(" ", strip=True) if company_node else ""

        # ★ 등록일(.job_day)은 .job_sector 안에 있다. 아래에서 노이즈로 떼어내므로
        #   반드시 그 전에 읽어야 한다.
        posted_node = item.select_one(self.SELECTORS["posted"])
        posted_text = posted_node.get_text(" ", strip=True) if posted_node else None

        sector = item.select_one(self.SELECTORS["sector"])
        keywords: list[str] = []
        if sector:
            noise = sector.select_one(self.SELECTORS["sector_noise"])
            if noise:
                noise.extract()
            keywords = [
                part.strip()
                for part in sector.get_text(",", strip=True).split(",")
                if part.strip() and part.strip() != "외"
            ]

        deadline_node = item.select_one(self.SELECTORS["deadline"])

        return RawJob(
            source=self.source,
            source_job_id=rec_idx,
            url=self.VIEW_URL.format(rec_idx=rec_idx),
            title=title,
            company_name=company,
            tech_stacks=keywords,
            job_categories=keywords,
            closed_at=_parse_deadline(
                deadline_node.get_text(" ", strip=True) if deadline_node else None
            ),
            published_at=_parse_list_posted_at(posted_text),
            raw={"list_condition": hu.block_text(item.select_one(".job_condition"))},
        )

    async def fetch_detail(self, job: RawJob) -> RawJob:
        rec_idx = job.source_job_id
        try:
            condition_soup = await self.get_soup(
                self.AJAX_URL, params={"rec_idx": rec_idx}, snapshot=f"cond_{rec_idx}"
            )
        except CrawlError as exc:
            log.warning("사람인 조건 수집 실패 rec_idx=%s: %s", rec_idx, exc)
            return job

        try:
            body_soup = await self.get_soup(
                self.BODY_URL,
                params={"rec_idx": rec_idx, "rec_seq": 0},
                snapshot=f"body_{rec_idx}",
            )
        except CrawlError as exc:
            log.warning("사람인 본문 수집 실패 rec_idx=%s: %s", rec_idx, exc)
            body_soup = None

        return self.merge_detail(job, condition_soup, body_soup)

    def merge_detail(
        self, job: RawJob, condition: BeautifulSoup, body: Optional[BeautifulSoup]
    ) -> RawJob:
        update: dict[str, Any] = {"detail_fetched": True}

        if posting := hu.jobposting_from_jsonld(condition):
            update |= self._from_jsonld(posting)

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
            # ★ 목록에서 등록일을 얻었으면 그대로 둔다. 여기 "시작일" 은 접수
            #   시작일이라 등록일과 다르고, 접수 예정 공고는 미래 날짜가 온다.
            "published_at": job.published_at or _parse_datetime(hu.pick(pairs, "posted_at")),
            "closed_at": _parse_datetime(hu.pick(pairs, "expires_at")) or job.closed_at,
            "company_tags": company_types,
            "company_industry": hu.pick(pairs, "industry"),
            "company_employee_count": hu.parse_employee_count(hu.pick(pairs, "employee")),
            "company_revenue": hu.parse_revenue(hu.pick(pairs, "revenue")),
            "company_establish_date": hu.pick(pairs, "founded"),
            "company_url": hu.pick(pairs, "homepage"),
        }

        company_node = condition.select_one("a[href*='company-info'], .company_nm, .corp_name")
        if company_node and (name := company_node.get_text(" ", strip=True)):
            update["company_name"] = name

        if csn := self._company_csn(condition):
            update["company_source_id"] = csn

        if body is not None:
            body = hu.strip_boilerplate(copy.copy(body))
            node = body.select_one(self.SELECTORS["body"]) or body.body
            text = hu.block_text(node)
            is_image, failed, images = hu.classify_body(
                node, text, is_valid=extractor.is_valid_body(text)
            )
            update |= {
                "responsibility": text or None,
                "body_is_image": is_image,
                "body_extract_failed": failed,
                "image_urls": images,
            }

        clean = {k: v for k, v in update.items() if v not in (None, "", [])}
        clean["detail_fetched"] = True
        clean["body_is_image"] = update.get("body_is_image", False)
        clean["body_extract_failed"] = update.get("body_extract_failed", body is None)
        return job.merged(clean)

    @staticmethod
    def _company_csn(soup: BeautifulSoup) -> Optional[str]:
        for anchor in soup.select("a[href*='company-info']"):
            if match := re.search(r"[?&]csn=([^&#]+)", anchor.get("href") or ""):
                return match.group(1)
        return None

    @staticmethod
    def _from_jsonld(posting: dict[str, Any]) -> dict[str, Any]:
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
    return datetime.now(UTC).astimezone(timezone(timedelta(hours=9)))
