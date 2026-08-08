"""수집 오케스트레이션: sites → extractor → market.repository (R2).

한 페이지 처리 흐름
    1. 목록 수집
    2. (옵션) 상세를 동시성 제한 하에 병렬 수집
    3. content_hash 비교
         같으면 → collected_at 만 갱신 (skipped)
         다르면 → company · company_source · job_posting · posting_skill upsert
    4. crawl_run 에 집계 기록

네트워크 I/O 를 먼저 끝내고 그다음에 DB 세션을 연다.
세션을 열어둔 채 HTTP 를 기다리면 커넥션을 오래 붙잡게 된다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from app.core import enums
from app.core.database import session_scope
from app.domains.crawler import extractor
from app.domains.crawler.extractor import SkillMatcher
from app.domains.crawler.schemas import RawJob
from app.domains.crawler.sites.base import (
    BaseSiteCrawler,
    CrawlError,
    ParseError,
    SelectorBrokenError,
)
from app.domains.market import repository

log = logging.getLogger(__name__)


@dataclass
class CrawlStats:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    # ★ 목록 단계에서 걸러 상세 요청 자체를 하지 않은 건수 (증분 수집).
    #   crawl_run 에는 별도 컬럼이 없으므로 skipped 에 합산하고 내역은
    #   message 에 남긴다. 명세에 없는 컬럼을 임의로 늘리지 않는다.
    skipped_known: int = 0
    # content_hash 는 같은데 파서가 바뀌어 다시 적재한 건수 (force_reextract).
    reextracted: int = 0
    errors: int = 0
    pages: int = 0
    total_available: int = 0
    error_messages: list[str] = field(default_factory=list)
    # 이번 실행에서 실제로 내용이 바뀐 공고. crawl_site 가 이 목록으로만
    # embed_postings 를 enqueue 한다 (해시가 같으면 재임베딩할 게 없다).
    changed_posting_ids: list[int] = field(default_factory=list)

    def as_line(self) -> str:
        return (
            f"fetched={self.fetched} inserted={self.inserted} updated={self.updated} "
            f"skipped={self.skipped} skipped_known={self.skipped_known} "
            f"reextracted={self.reextracted} errors={self.errors}"
        )


@dataclass
class ReparseStats:
    postings: int = 0
    skills_linked: int = 0
    without_skills: int = 0
    field_updated: int = 0
    body_failed: int = 0
    errors: int = 0

    def as_line(self) -> str:
        return (
            f"postings={self.postings} skills_linked={self.skills_linked} "
            f"without_skills={self.without_skills} field_updated={self.field_updated} "
            f"body_failed={self.body_failed} errors={self.errors}"
        )


async def reparse_skills(*, source: str | None = None, limit: int | None = None) -> ReparseStats:
    """이미 저장된 본문으로 스택과 분야를 다시 계산한다. 재수집하지 않는다.

    사전이나 분류 규칙을 고친 뒤 돌린다.
    posting_skill 을 갈아끼우고 job_posting.field_id 만 갱신한다.
    본문·회사 등 수집 원본은 건드리지 않는다.
    """
    stats = ReparseStats()

    async with session_scope() as session:
        matcher = SkillMatcher.from_rows(await repository.load_skill_entries(session))
        field_ids = await repository.get_field_ids(session)
        targets = await repository.iter_postings_for_reparse(session, source=source, limit=limit)

    log.info("재추출 대상 %d건 (사전 %d스킬)", len(targets), len(matcher.entries))

    async with session_scope() as session:
        for posting_id, description, tags, raw_fields, old_field_id in targets:
            try:
                async with session.begin_nested():
                    hits = matcher.extract(description=description, tags=tags)
                    await repository.replace_posting_skills(
                        session,
                        posting_id,
                        [(h.skill_id, h.requirement, h.mentions) for h in hits],
                    )

                    # 분야는 수집 시점 규칙으로 굳어 있다. 규칙이 바뀌었을 수 있으니
                    # 원본 직무분류에서 다시 계산한다.
                    tech_field = extractor.map_tech_field(raw_fields.get("job_categories") or [])
                    new_field_id = field_ids.get(tech_field.value) if tech_field else None
                    if new_field_id != old_field_id:
                        await repository.update_posting_field(session, posting_id, new_field_id)
                        stats.field_updated += 1

                    # 저장된 본문이 실제로 '요강' 인지 다시 판정한다.
                    # 길이 기준으로 통과했던 과거 행을 여기서 바로잡는다.
                    if not extractor.is_valid_body(description, matcher):
                        if await repository.mark_body_extract_failed(session, posting_id):
                            stats.body_failed += 1
            except Exception:
                log.exception("재추출 실패 posting_id=%s", posting_id)
                stats.errors += 1
                continue

            stats.postings += 1
            stats.skills_linked += len(hits)
            if not hits:
                stats.without_skills += 1

    return stats


async def rebuild_bodies_from_snapshots(crawler: BaseSiteCrawler) -> ReparseStats:
    """저장된 HTML 스냅샷으로 본문만 다시 뽑는다. 네트워크를 타지 않는다.

    파서를 고친 뒤 재수집 없이 결과를 반영하려고 쓴다.
    description · body_is_image · body_extract_failed 만 갱신하고
    나머지 컬럼은 건드리지 않는다.
    """
    stats = ReparseStats()
    paths = sorted(crawler.snapshot_dir.glob("position_*.html"))
    log.info("%s: 스냅샷 %d건에서 본문 재추출", crawler.source, len(paths))
    if not paths:
        return stats

    extract_body = getattr(crawler, "extract_body", None)
    if extract_body is None:
        raise RuntimeError(f"{crawler.source} 는 extract_body() 를 제공하지 않습니다.")

    async with session_scope() as session:
        for path in paths:
            source_job_id = path.stem.removeprefix("position_")
            try:
                soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
                result = extract_body(soup)
                async with session.begin_nested():
                    updated = await repository.update_posting_body(
                        session,
                        source=crawler.source,
                        source_job_id=source_job_id,
                        description=result.get("responsibility"),
                        body_is_image=bool(result.get("body_is_image")),
                        body_extract_failed=bool(result.get("body_extract_failed")),
                    )
            except Exception:
                log.exception("본문 재추출 실패 %s", path.name)
                stats.errors += 1
                continue

            if updated:
                stats.postings += 1
                if result.get("body_extract_failed"):
                    stats.without_skills += 1

    return stats


class CrawlService:
    def __init__(self, crawler: BaseSiteCrawler, *, force_reextract: bool = False) -> None:
        self.crawler = crawler
        # ★ 파서를 고친 뒤 백필용. content_hash 가 같아도 다시 적재한다.
        #   content_hash 는 사이트 원문으로만 계산하므로 우리 파서가 바뀐 것을
        #   감지하지 못한다 — 그대로 두면 "재수집했는데 아무것도 안 바뀜" 이 된다.
        self.force_reextract = force_reextract

    async def crawl(
        self,
        pages: int = 1,
        *,
        with_detail: bool = True,
        start_page: int = 1,
        skip_seen_days: int = 0,
        keyword: str | None = None,
    ) -> CrawlStats:
        """한 사이트(+키워드) 를 pages 만큼 순회한다.

        skip_seen_days > 0 이면 최근 그 기간에 이미 수집한 공고를 **목록
        단계에서** 걸러 상세 요청을 아낀다. 상세를 받은 뒤 content_hash 를
        비교하는 방식은 요청이 이미 나간 뒤라 절감 효과가 없다.
        """
        stats = CrawlStats()

        async with session_scope() as session:
            run_id = await repository.start_run(
                session,
                kind=enums.RunKind.CRAWL,
                source=self.crawler.source,
                keyword=keyword,
            )
            field_ids = await repository.get_field_ids(session)
            # 별칭 사전은 실행당 한 번만 읽어 정규식으로 컴파일한다.
            matcher = SkillMatcher.from_rows(await repository.load_skill_entries(session))
            # 증분 수집용 기수집 id 집합. 실행당 1회만 조회한다.
            seen = await repository.recent_source_ids(
                session, self.crawler.source, days=skip_seen_days
            )
        if seen:
            log.info(
                "%s: 최근 %d일 내 수집 %d건 — 목록에서 걸러 상세 요청을 생략합니다.",
                self.crawler.source,
                skip_seen_days,
                len(seen),
            )

        try:
            for page in range(start_page, start_page + pages):
                try:
                    jobs, total = await self.crawler.fetch_list_page(page)
                except ParseError as exc:
                    # 사이트 개편. 이 사이트는 더 진행해도 의미가 없다.
                    log.error("%s: 목록 구조 변경 — 중단합니다. %s", self.crawler.source, exc)
                    stats.errors += 1
                    stats.error_messages.append(str(exc))
                    break
                except CrawlError as exc:
                    log.warning(
                        "%s: page %d 수집 실패, 다음 페이지로. %s", self.crawler.source, page, exc
                    )
                    stats.errors += 1
                    stats.error_messages.append(str(exc))
                    continue

                stats.pages += 1
                stats.total_available = total or stats.total_available
                log.info("%s %d페이지: %d건", self.crawler.source, page, len(jobs))

                # ── 종료 조건 (명세 3-3) ──────────────────────────────────
                if not jobs:
                    if page == start_page:
                        # 검색 결과 없음이 아니라 파싱이 깨진 것이다.
                        raise SelectorBrokenError(self.crawler.source, keyword)
                    log.info("%s: page %d 가 비어 있어 종료합니다.", self.crawler.source, page)
                    break

                # ── 증분 필터 (명세 3-2) ─────────────────────────────────
                fresh = [job for job in jobs if job.source_job_id not in seen]
                known = len(jobs) - len(fresh)
                if known:
                    stats.skipped_known += known
                    stats.skipped += known
                    stats.fetched += known
                if not fresh:
                    log.info(
                        "%s: page %d 전부 기수집(%d건) — 상세 생략",
                        self.crawler.source,
                        page,
                        known,
                    )
                    continue

                if with_detail:
                    fresh = await self._fetch_details(fresh, stats)

                await self._persist(fresh, field_ids, matcher, stats)
                log.info("%s: page %d 완료 — %s", self.crawler.source, page, stats.as_line())

        except Exception as exc:  # 예상 못 한 실패도 run 을 닫아야 한다
            stats.errors += 1
            stats.error_messages.append(repr(exc))
            await self._close_run(run_id, stats, enums.RunStatus.FAILED)
            raise

        status = (
            enums.RunStatus.SUCCESS
            if stats.errors == 0
            else (enums.RunStatus.PARTIAL if stats.fetched else enums.RunStatus.FAILED)
        )
        await self._close_run(run_id, stats, status)
        return stats

    # ── 상세 ──────────────────────────────────────────────────────────────
    async def _fetch_details(self, jobs: list[RawJob], stats: CrawlStats) -> list[RawJob]:
        """동시성 제한은 crawler 내부 세마포어가 건다."""
        results = await asyncio.gather(
            *(self.crawler.fetch_detail(job) for job in jobs), return_exceptions=True
        )
        merged: list[RawJob] = []
        for original, result in zip(jobs, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("상세 실패 id=%s: %r", original.source_job_id, result)
                stats.errors += 1
                merged.append(original)  # 목록 데이터만으로 저장
            else:
                merged.append(result)
        return merged

    # ── 적재 ──────────────────────────────────────────────────────────────
    async def _persist(
        self,
        jobs: list[RawJob],
        field_ids: dict[str, int],
        matcher: SkillMatcher,
        stats: CrawlStats,
    ) -> None:
        async with session_scope() as session:
            known = await repository.get_content_hashes(
                session, self.crawler.source, [j.source_job_id for j in jobs]
            )

            for job in jobs:
                stats.fetched += 1
                new_hash = job.content_hash()
                old_hash = known.get(job.source_job_id)
                unchanged = old_hash is not None and old_hash == new_hash

                # 내용이 그대로면 재추출을 건너뛴다 — 단, 파서를 고친 직후에는
                # 그러면 안 된다. content_hash 는 사이트가 준 원문으로만 계산해서
                # 우리 파서가 바뀐 것을 모른다(employment_type 은 해시에 아예
                # 없다). 그래서 파서 수정 후 백필은 force_reextract 로 돈다.
                if unchanged and not self.force_reextract:
                    await repository.touch_posting(session, self.crawler.source, job.source_job_id)
                    stats.skipped += 1
                    continue

                try:
                    # SAVEPOINT. postgres 는 문 하나가 실패하면 트랜잭션 전체가
                    # abort 되므로, 중첩 트랜잭션 없이 try/except 만 두면 이후
                    # 모든 공고가 InFailedSqlTransaction 으로 연쇄 실패한다.
                    async with session.begin_nested():
                        posting_id = await self._save_one(
                            session, job, field_ids, matcher, new_hash
                        )
                except Exception as exc:  # 한 건 실패가 페이지 전체를 막지 않게
                    log.exception("적재 실패 id=%s", job.source_job_id)
                    stats.errors += 1
                    stats.error_messages.append(f"{job.source_job_id}: {exc!r}")
                    continue

                if unchanged:
                    # 해시는 그대로인데 파서가 바뀌어 다시 쓴 것. 본문은 그대로라
                    # 재임베딩할 게 없으므로 changed_posting_ids 에 넣지 않는다.
                    # (넣으면 사람인 전체가 임베딩 큐로 들어간다)
                    stats.reextracted += 1
                    continue

                stats.changed_posting_ids.append(posting_id)
                if old_hash is None:
                    stats.inserted += 1
                else:
                    stats.updated += 1

    async def _save_one(
        self,
        session,
        job: RawJob,
        field_ids: dict[str, int],
        matcher: SkillMatcher,
        content_hash: str,
    ) -> int:
        company_id = await self._save_company(session, job)

        tech_field = extractor.map_tech_field(job.job_categories)
        description = job.build_description()
        salary_min, salary_max, salary_type, salary_period = extractor.parse_salary(job.salary_raw)

        posting_id = await repository.upsert_posting(
            session,
            {
                "source": job.source,
                "source_job_id": job.source_job_id,
                "company_id": company_id,
                # 분류에 못 맞추면 NULL 로 둔다 ("기타" 버킷을 만들지 않는다)
                "field_id": field_ids.get(tech_field.value) if tech_field else None,
                "title": job.title[:300],
                "company_name_raw": job.company_name[:200] or None,
                "tags_raw": job.tech_stacks,
                "career_min": job.career_min,
                "career_max": job.career_max,
                # 사람인 학력 라벨이 "대졸(2,3년제) 이상 (졸업예정자 가능)" 처럼 길다.
                "education": (job.education or None) and job.education[:30],
                "employment_type": (job.employment_type or None) and job.employment_type[:30],
                "location": (job.locations[0][:120] if job.locations else None),
                "description": description,
                "welfare": job.build_welfare(),
                "salary_raw": job.salary_raw,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_type": salary_type.value,
                "salary_period": salary_period.value if salary_period else None,
                "body_is_image": job.body_is_image,
                "body_extract_failed": job.body_extract_failed,
                "posted_at": job.published_at,
                "expires_at": job.closed_at,
                "content_hash": content_hash,
                "collected_at": datetime.now(UTC),
                "raw_fields": {
                    "url": job.url,
                    "job_categories": job.job_categories,
                    "locations": job.locations,
                    "company_tags": job.company_tags,
                    "newcomer": job.newcomer,
                    "detail_fetched": job.detail_fetched,
                    "image_urls": job.image_urls,
                },
            },
        )

        # 본문 + 사이트 태그에서 스킬 추출.
        # 사전에 없는 태그는 버린다. 사전이 유일한 권위이고, 원본은
        # job_posting.tags_raw 에 그대로 남아 있어 나중에 사전을 늘리면 복구된다.
        hits = matcher.extract(description=description, tags=job.tech_stacks)
        await repository.replace_posting_skills(
            session,
            posting_id,
            [(hit.skill_id, hit.requirement, hit.mentions) for hit in hits],
        )
        return posting_id

    async def _save_company(self, session, job: RawJob) -> int | None:
        if not job.company_name.strip():
            return None

        name_key = extractor.normalize_company_name(job.company_name)
        if not name_key:
            return None

        company_id = await repository.upsert_company(
            session,
            name=job.company_name[:200],
            name_key=name_key[:200],
            description=job.company_service_info,
            homepage=(job.company_url or None),
            founded=(job.company_establish_date or None),
            industry=job.company_industry,
            employee_count=job.company_employee_count,
            revenue=job.company_revenue,
            # 사원수를 알면 그쪽이 정확하다. 없으면 "대기업" 같은 태그로 추정한다.
            size_type=extractor.company_size(
                employee_count=job.company_employee_count, tags=job.company_tags
            ),
        )

        # 사이트 내부 기업 id 는 상세에만 있다. 없으면 원장을 남기지 않는다.
        if job.company_source_id:
            await repository.upsert_company_source(
                session,
                company_id=company_id,
                source=job.source,
                source_company_id=job.company_source_id,
                url=job.company_url,
            )
        return company_id

    # ── 이력 ──────────────────────────────────────────────────────────────
    async def _close_run(self, run_id: int, stats: CrawlStats, status: enums.RunStatus) -> None:
        # skipped 는 "해시 동일" 과 "목록에서 걸러 상세를 안 받음" 두 가지가
        # 섞인 값이다. 내역을 message 에 남겨 둬야 나중에 증분 효과를 읽을 수
        # 있다 (crawl_run 에 컬럼을 늘리지 않는다).
        notes = [f"skipped_known={stats.skipped_known}"] if stats.skipped_known else []
        notes += stats.error_messages[:5]

        async with session_scope() as session:
            await repository.finish_run(
                session,
                run_id,
                status=status,
                fetched=stats.fetched,
                inserted=stats.inserted,
                updated=stats.updated,
                skipped=stats.skipped,
                errors=stats.errors,
                message="; ".join(notes) or None,
            )
