# 원티드 목록과 상세 수집 (JSON API)

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites.base import BaseSiteCrawler, CrawlError, ParseError

log = logging.getLogger(__name__)

BASE = "https://www.wanted.co.kr"
LIST_URL = f"{BASE}/api/chaos/navigation/v1/results"
DETAIL_URL = f"{BASE}/api/chaos/jobs/v1/{{job_id}}/details"
WEB_URL = f"{BASE}/wd/{{job_id}}"

JOB_GROUP_DEV = 518
PAGE_SIZE = 20

KST = timezone(timedelta(hours=9))

_EMPLOYMENT = {
    "regular": "정규직",
    "contract": "계약직",
    "intern": "인턴",
    "freelance": "프리랜서",
}


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _titles(tags: Any) -> list[str]:
    # 태그 키가 응답마다 title, name, text 로 달라 셋 다 본다
    result: list[str] = []
    for tag in tags or []:
        if isinstance(tag, dict) and (title := tag.get("title") or tag.get("name") or tag.get("text")):
            result.append(str(title).strip())
        elif isinstance(tag, str) and tag.strip():
            result.append(tag.strip())
    return result


def _job_categories(category_tag: Any) -> list[str]:
    if not isinstance(category_tag, dict):
        return []
    return _titles(category_tag.get("child_tags"))


def _location(address: Any) -> str | None:
    if not isinstance(address, dict):
        return None
    if full := address.get("full_location"):
        return str(full)
    parts = [address.get("location"), address.get("district")]
    joined = " ".join(str(p) for p in parts if p)
    return joined or None


class WantedCrawler(BaseSiteCrawler):
    source = "wanted"
    referer = f"{BASE}/wdlist/{JOB_GROUP_DEV}"

    def __init__(self, *, job_group_id: int = JOB_GROUP_DEV, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.job_group_id = job_group_id

    async def fetch_list_page(self, page: int) -> tuple[list[RawJob], int]:
        payload = await self.get_json(
            LIST_URL,
            params={
                "job_group_id": self.job_group_id,
                "country": "kr",
                "job_sort": "job.latest_order",
                "years": -1,
                "locations": "all",
                "limit": PAGE_SIZE,
                "offset": (page - 1) * PAGE_SIZE,
            },
            snapshot=f"list_p{page}",
        )
        return self.parse_list(payload, page)

    def parse_list(self, payload: Any, page: int) -> tuple[list[RawJob], int]:
        if not isinstance(payload, dict) or "data" not in payload:
            raise ParseError(
                f"원티드 목록 응답에 data 가 없습니다 (page={page}). "
                f"받은 키: {list((payload or {}).keys())}"
            )

        jobs: list[RawJob] = []
        for item in payload.get("data") or []:
            try:
                jobs.append(self._map_list_item(item))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("원티드 목록 항목 매핑 실패 id=%s: %s", item.get("id"), exc)
        return jobs, 0

    def _map_list_item(self, item: dict[str, Any]) -> RawJob:
        job_id = item["id"]
        company = item.get("company") or {}
        employment = item.get("employment_type")
        return RawJob(
            source=self.source,
            source_job_id=str(job_id),
            url=WEB_URL.format(job_id=job_id),
            title=item.get("position") or "",
            company_name=(company.get("name") or "").strip(),
            tech_stacks=_titles(item.get("skill_tags")),
            locations=[loc for loc in [_location(item.get("address"))] if loc],
            career_min=item.get("annual_from"),
            career_max=item.get("annual_to"),
            newcomer=bool(item.get("is_newbie")),
            employment_type=_EMPLOYMENT.get(employment or "", employment),
            company_source_id=str(company["id"]) if company.get("id") else None,
            raw={"list": item},
        )

    async def fetch_detail(self, job: RawJob) -> RawJob:
        try:
            payload = await self.get_json(
                DETAIL_URL.format(job_id=job.source_job_id),
                snapshot=f"position_{job.source_job_id}",
            )
        except CrawlError as exc:
            log.warning("원티드 상세 실패 id=%s: %s", job.source_job_id, exc)
            return job
        return self.merge_detail(job, payload)

    def merge_detail(self, job: RawJob, payload: Any) -> RawJob:
        posting = (payload or {}).get("job")
        if not isinstance(posting, dict):
            log.warning("원티드 상세 응답에 job 이 없음 id=%s", job.source_job_id)
            return job

        detail = posting.get("detail") or {}
        company = posting.get("company") or {}

        responsibility = detail.get("main_tasks")
        requirements = detail.get("requirements")
        preferred = detail.get("preferred_points")

        update: dict[str, Any] = {
            "detail_fetched": True,
            "title": detail.get("position") or job.title,
            "company_name": company.get("name") or job.company_name,
            "tech_stacks": _titles(posting.get("skill_tags")) or job.tech_stacks,
            "job_categories": _job_categories(posting.get("category_tag")) or job.job_categories,
            "locations": [loc for loc in [_location(posting.get("address"))] if loc]
            or job.locations,
            "career_min": posting.get("annual_from", job.career_min),
            "career_max": posting.get("annual_to", job.career_max),
            "newcomer": bool(posting.get("is_newbie", job.newcomer)),
            "closed_at": _parse_dt(posting.get("due_time")) or job.closed_at,
            "responsibility": responsibility,
            "qualifications": requirements,
            "preferred_requirements": preferred,
            "welfares": detail.get("benefits"),
            "recruit_process": detail.get("hire_rounds"),
            "company_service_info": detail.get("intro"),
            "company_industry": company.get("industry_name"),
            "company_tags": _titles(company.get("company_tags")),
            "company_source_id": (
                str(company["id"]) if company.get("id") else job.company_source_id
            ),
            "body_extract_failed": not any((responsibility, requirements, preferred)),
            "raw": {**job.raw, "detail": detail},
        }

        clean = {k: v for k, v in update.items() if v not in (None, "", [])}
        clean["detail_fetched"] = True
        clean["body_extract_failed"] = update["body_extract_failed"]
        return job.merged(clean)
