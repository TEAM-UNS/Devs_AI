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
    # ★ 구분자가 목록 종류마다 다르다. 검색은 "10/11", 분류 목록은 "~10.30(금)".
    #   "채용시" · "오늘마감" · "D-6" 은 날짜가 없어 None 으로 둔다.
    match = re.search(r"(\d{1,2})[./](\d{1,2})", text)
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
    # ★ 검색(/zf_user/search)이 아니라 직무분류 목록을 쓴다.
    #   검색은 단어를 넣어야 결과가 나와서, 그 단어에 안 걸리는 공고는 아무리
    #   깊이 파도 영원히 안 보인다. 실측: IT개발·데이터 아래 직무 28종 중
    #   기존 키워드 8개로 닿는 것은 4종뿐이었다(웹개발 1,380건 · 기술지원 1,723건
    #   · SE 1,282건 · QA/테스터 398건 … 합계 12,419건이 사각지대).
    #   분류 목록은 cat_mcls=2 하나로 IT개발·데이터 전량(11,154건)을 준다.
    LIST_URL = f"{BASE}/zf_user/jobs/list/job-category"
    # 중분류 코드. 사람인 분류 체계에서 2 = IT개발·데이터
    CAT_MCLS_IT = 2
    # 상세 페이지 조건 영역은 JS 렌더링이라 그 영역을 채우는 ajax 를 직접 부른다
    AJAX_URL = f"{BASE}/zf_user/jobs/relay/view-ajax"
    BODY_URL = f"{BASE}/zf_user/jobs/relay/view-detail"
    VIEW_URL = f"{BASE}/zf_user/jobs/relay/view?rec_idx={{rec_idx}}"
    # 분류 목록은 recruitPageCount 를 무시하고 50건 고정으로 준다 (실측)
    PAGE_SIZE = 50
    SELECTORS = {
        "item": ".list_item",
        "title_link": ".job_tit a",
        # ★ a.str_tit 로 좁히면 안 된다. 기업정보 페이지가 없는 곳은 span.str_tit 이다
        #   (실측: 50건 중 2건 — 삼성서울병원 · 블랑코컴퍼니)
        "company": ".company_nm .str_tit",
        "sector": ".job_sector",
        "work_place": ".work_place",
        "career": ".career",
        "education": ".education",
        "deadline": ".support_detail .date",
        "body": ".user_content",
    }

    referer = f"{BASE}/"
    concurrency = 2

    # ★ keyword 를 받지 않는다. 분류 목록(cat_mcls=2)이 IT개발·데이터 전량을
    #   주므로 검색어가 필요 없다. 점핏·원티드와 같은 모양이 됐다.

    def default_headers(self) -> dict[str, str]:
        headers = super().default_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        headers["X-Requested-With"] = "XMLHttpRequest"
        return headers

    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        # ★ 응답이 {"total_count": N, "contents": "<HTML>"} 형태다.
        #   HTML 조각이 JSON 안에 들어 있어 get_soup 을 바로 못 쓴다.
        payload = await self.get_json(
            self.LIST_URL,
            params={"cat_mcls": self.CAT_MCLS_IT, "page": page},
            snapshot=f"list_p{page}",
        )
        if not isinstance(payload, dict) or "contents" not in payload:
            raise ParseError(
                f"사람인 분류 목록 응답에 contents 가 없습니다 (page={page}). "
                f"받은 키: {list((payload or {}).keys())}"
            )
        total = int(payload.get("total_count") or 0)
        return self.parse_list(BeautifulSoup(payload["contents"], "lxml"), page, total)

    def parse_list(
        self, soup: BeautifulSoup, page: int, total: int = 0
    ) -> tuple[list[RawJob], int]:
        items = soup.select(self.SELECTORS["item"])
        if not items:
            # 결과 소진(마지막 페이지 다음)과 구조 변경을 구분해야 한다.
            # 총 건수는 왔는데 항목이 없으면 소진으로 본다.
            if total and page > 1:
                return [], total
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

        return jobs, total

    def _map_list_item(self, item) -> Optional[RawJob]:
        link = item.select_one(self.SELECTORS["title_link"]) or item.select_one("a[href*='rec_idx=']")
        rec_idx = _rec_idx(link.get("href") if link else None)
        if not rec_idx:
            return None

        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        company_node = item.select_one(self.SELECTORS["company"])
        company = company_node.get_text(" ", strip=True) if company_node else ""

        # 분류 목록은 직무 키워드를 <span> 으로 하나씩 준다. 마지막 "외" 는 버린다.
        sector = item.select_one(self.SELECTORS["sector"])
        keywords: list[str] = []
        if sector:
            keywords = [
                text
                for span in sector.select("span")
                if (text := span.get_text(" ", strip=True)) and text != "외"
            ]

        def text_of(key: str) -> str:
            node = item.select_one(self.SELECTORS[key])
            return node.get_text(" ", strip=True) if node else ""

        deadline_node = item.select_one(self.SELECTORS["deadline"])
        work_place = text_of("work_place")

        return RawJob(
            source=self.source,
            source_job_id=rec_idx,
            url=self.VIEW_URL.format(rec_idx=rec_idx),
            title=title,
            company_name=company,
            tech_stacks=keywords,
            job_categories=keywords,
            locations=[work_place] if work_place else [],
            closed_at=_parse_deadline(
                deadline_node.get_text(" ", strip=True) if deadline_node else None
            ),
            # 상세가 비거나 실패해도 목록에서 본 조건은 남겨 둔다
            raw={
                "list_condition": " · ".join(
                    v for v in (work_place, text_of("career"), text_of("education")) if v
                )
            },
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
            "published_at": _parse_datetime(hu.pick(pairs, "posted_at")),
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
