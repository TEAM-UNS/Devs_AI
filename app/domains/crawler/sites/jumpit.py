"""점핏 — 공개 XHR(JSON) 호출.

실제 응답을 확인하고 필드명을 고정했다 (2026-07-30 기준).

    목록  GET https://jumpit-api.saramin.co.kr/api/positions
              ?sort=popular&highlight=false&page=N
          → result.{totalCount, page, positions[]}
          positions[]: id · title · companyName · techStacks(문자열 배열)
                       jobCategory(콤마 문자열) · locations[] · minCareer · maxCareer
                       newcomer · closedAt · serialNumber

    상세  GET https://jumpit-api.saramin.co.kr/api/position/{id}
          → result.{responsibility, qualifications, preferredRequirements,
                    welfares, recruitProcess, serviceInfo, location, tags[],
                    jobCategories[{id,name}], companyUrl, establishDate,
                    educationName, publishedAt, manDbMcomIdx}

주의
    - techStacks 의 형태가 목록(문자열 배열)과 상세(`{stack, imagePath}` 객체 배열)로
      다르다. 상세로 덮어쓸 때 반드시 변환해야 한다.
    - 연봉 필드가 아예 없다. 점핏은 급여를 공개하지 않으므로
      salary_type 은 항상 unknown 이 된다.
    - 날짜 표기도 목록("...T23:59:59")과 상세("... 23:59:59")가 다르다.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites.base import BaseSiteCrawler, CrawlError, ParseError

log = logging.getLogger(__name__)

API_BASE = "https://jumpit-api.saramin.co.kr"
LIST_URL = f"{API_BASE}/api/positions"
DETAIL_URL = f"{API_BASE}/api/position/{{id}}"
WEB_BASE = "https://jumpit.saramin.co.kr"

PAGE_SIZE = 16

KST = timezone(timedelta(hours=9))


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "T"))
    except ValueError:
        log.debug("날짜 파싱 실패: %r", value)
        return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt


def _split_categories(value: Any) -> list[str]:
    if not value or not isinstance(value, str):
        return []
    return [c.strip() for c in value.split(",") if c.strip()]


class JumpitCrawler(BaseSiteCrawler):
    source = "jumpit"
    referer = f"{WEB_BASE}/positions"

    # ── 목록 ──────────────────────────────────────────────────────────────
    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        payload = await self.get_json(
            LIST_URL,
            params={"sort": "popular", "highlight": "false", "page": page},
            snapshot=f"list_p{page}",
        )
        return self.parse_list(payload, page)

    def parse_list(self, payload: Any, page: int) -> tuple[list[RawJob], int]:
        result = (payload or {}).get("result")
        if not isinstance(result, dict) or "positions" not in result:
            raise ParseError(
                f"jumpit 목록 응답에 result.positions 가 없습니다 (page={page}). "
                f"받은 키: {list((payload or {}).keys())}"
            )

        positions = result.get("positions") or []
        total = int(result.get("totalCount") or 0)

        jobs: list[RawJob] = []
        for item in positions:
            try:
                jobs.append(self._map_list_item(item))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("jumpit 목록 항목 매핑 실패 id=%s: %s", item.get("id"), exc)
        return jobs, total

    def _map_list_item(self, item: dict[str, Any]) -> RawJob:
        job_id = item["id"]
        stacks = item.get("techStacks") or []
        return RawJob(
            source=self.source,
            source_job_id=str(job_id),
            url=f"{WEB_BASE}/position/{job_id}",
            title=item.get("title") or "",
            company_name=item.get("companyName") or "",
            tech_stacks=[s for s in stacks if isinstance(s, str)],
            job_categories=_split_categories(item.get("jobCategory")),
            locations=[loc for loc in (item.get("locations") or []) if isinstance(loc, str)],
            career_min=item.get("minCareer"),
            career_max=item.get("maxCareer"),
            newcomer=bool(item.get("newcomer")),
            closed_at=_parse_dt(item.get("closedAt")),
            raw={"list": item},
        )

    # ── 상세 ──────────────────────────────────────────────────────────────
    async def fetch_detail(self, job: RawJob) -> RawJob:
        try:
            payload = await self.get_json(
                DETAIL_URL.format(id=job.source_job_id),
                snapshot=f"position_{job.source_job_id}",
            )
        except CrawlError as exc:
            log.warning("jumpit 상세 실패 id=%s: %s", job.source_job_id, exc)
            return job
        return self.merge_detail(job, payload)

    def merge_detail(self, job: RawJob, payload: Any) -> RawJob:
        result = (payload or {}).get("result")
        if not isinstance(result, dict):
            log.warning("jumpit 상세 응답에 result 없음 id=%s", job.source_job_id)
            return job

        stacks = [
            s.get("stack")
            for s in (result.get("techStacks") or [])
            if isinstance(s, dict) and s.get("stack")
        ]
        categories = [
            c.get("name")
            for c in (result.get("jobCategories") or [])
            if isinstance(c, dict) and c.get("name")
        ]
        tags = [
            t.get("name")
            for t in (result.get("tags") or [])
            if isinstance(t, dict) and t.get("name")
        ]
        location = result.get("location")
        man_idx = result.get("manDbMcomIdx")

        return job.merged(
            {
                "detail_fetched": True,
                "title": result.get("title") or job.title,
                "company_name": result.get("companyName") or job.company_name,
                "tech_stacks": stacks or job.tech_stacks,
                "job_categories": categories or job.job_categories,
                "locations": [location]
                if isinstance(location, str) and location
                else job.locations,
                "career_min": result.get("minCareer", job.career_min),
                "career_max": result.get("maxCareer", job.career_max),
                "newcomer": bool(result.get("newcomer", job.newcomer)),
                "education": result.get("educationName"),
                "published_at": _parse_dt(result.get("publishedAt")),
                "closed_at": _parse_dt(result.get("closedAt")) or job.closed_at,
                "responsibility": result.get("responsibility"),
                "qualifications": result.get("qualifications"),
                "preferred_requirements": result.get("preferredRequirements"),
                "welfares": result.get("welfares"),
                "recruit_process": result.get("recruitProcess"),
                "company_service_info": result.get("serviceInfo"),
                "company_url": result.get("companyUrl"),
                "company_establish_date": result.get("establishDate"),
                "company_source_id": str(man_idx) if man_idx else None,
                "company_tags": tags,
                "raw": {**job.raw, "detail": result},
            }
        )
