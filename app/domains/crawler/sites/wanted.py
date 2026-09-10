"""원티드 — 공개 XHR(JSON) 호출. HTML 파싱 불필요.

실제 응답을 확인하고 필드명을 고정했다 (2026-07-30 기준).

    목록  GET /api/chaos/navigation/v1/results
              ?job_group_id=518&country=kr&job_sort=job.latest_order
              &years=-1&locations=all&limit=20&offset=N
          → data[]: id · position · company{id,name} · address{location,district}
                    skill_tags[] · annual_from/to · is_newbie · employment_type

    상세  GET /api/chaos/jobs/v1/{id}/details
          → job.detail{position, intro, main_tasks, requirements,
                       preferred_points, benefits, hire_rounds}
            job.company{name, industry_name, company_tags[]}
            job.address.full_location · job.due_time · job.skill_tags[]

점핏과 같은 패턴이라 sites/jumpit.py 의 구조를 그대로 따른다.

원티드의 장점
    본문이 이미 섹션별로 나뉘어 온다. HTML 사이트처럼 섹션 헤더를 찾아
    쪼갤 필요가 없다. RawJob.build_description() 이 "[자격요건]" 같은 헤더를
    붙여 합치므로 추출기가 그대로 인식한다.

주의
    - benefits(복지)는 description 에 넣지 않는다. 복지 문구의 "Slack 으로 소통",
      "자바 도서 지원" 이 스택으로 잡히면 집계가 오염된다. welfare 컬럼으로 간다.
    - skill_tags 는 [{id, title}] 또는 [] 로 온다. 빈 공고가 꽤 있다.
"""

from __future__ import annotations

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
    """태그 배열에서 표시 문자열만 뽑는다.

    ★ 키 이름이 응답마다 다르다. company_tags·attraction_tags 는 "title" 인데
      skill_tags·category_tag.child_tags 는 "text" 다. "text" 를 빠뜨렸던 탓에
      원티드 공고 전량(1,349건)의 스택 태그가 조용히 버려지고 있었다
      — tags_raw 가 비어도 수집은 성공으로 끝나서 드러나지 않았다.
    """
    result: list[str] = []
    for tag in tags or []:
        if isinstance(tag, dict) and (title := tag.get("title") or tag.get("name") or tag.get("text")):
            result.append(str(title).strip())
        elif isinstance(tag, str) and tag.strip():
            result.append(tag.strip())
    return result


def _job_categories(category_tag: Any) -> list[str]:
    """job.category_tag.child_tags → 직무 카테고리 목록.

    parent_tag 는 "개발" 고정이라 분류에 쓸 수 없다. child_tags 가
    "서버 개발자" · "머신러닝 엔지니어" 같은 실제 직무다.
    """
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

    # ── 목록 ──────────────────────────────────────────────────────────────
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

    # ── 상세 ──────────────────────────────────────────────────────────────
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
