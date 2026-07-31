"""크롤러 CLI.

    python -m app.cli inspect --site jumpit [--page 1] [--limit 2]
    python -m app.cli crawl   --site jumpit --pages 2 [--no-detail]

inspect 는 DB 를 건드리지 않는다. 실제 응답을 data/raw/ 에 저장하고
파서가 뽑아낸 값을 사람이 눈으로 확인하는 용도다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from app.core.database import dispose_engines, ensure_selector_event_loop_policy, session_scope
from app.domains.crawler.service import (
    CrawlService,
    rebuild_bodies_from_snapshots,
    reparse_skills,
)
from app.domains.crawler.sites.base import BaseSiteCrawler
from app.domains.crawler.sites.jobkorea import JobkoreaCrawler
from app.domains.crawler.sites.jumpit import JumpitCrawler
from app.domains.crawler.sites.saramin import SaraminCrawler
from app.domains.crawler.sites.wanted import WantedCrawler
from app.domains.market import repository

log = logging.getLogger(__name__)

# Windows: psycopg async 는 SelectorEventLoop 를 요구한다. 루프 생성 전에 호출.
ensure_selector_event_loop_policy()

SITES: dict[str, type[BaseSiteCrawler]] = {
    "jumpit": JumpitCrawler,
    "wanted": WantedCrawler,
    "saramin": SaraminCrawler,
    # "jobkorea": JobkoreaCrawler,
    #   본문이 HTML에 없음(JS 별도 엔드포인트). 유효 수율 18%로 비활성.
    #   스냅샷 103건 보존 (data/raw/jobkorea/). 엔드포인트를 찾으면 되살린다.
    #   sites/jobkorea.py 와 fixture·테스트는 그대로 유지한다.
}
# 키워드 검색이 필요한 사이트 (점핏·원티드는 직군 목록을 그대로 돈다)
KEYWORD_SITES = {"saramin", "jobkorea"}

# 비활성 사이트도 스냅샷 재파싱·테스트에는 쓸 수 있게 남겨둔다.
DISABLED_SITES: dict[str, type[BaseSiteCrawler]] = {"jobkorea": JobkoreaCrawler}


def _build_crawler(site: str, keyword: str | None = None) -> BaseSiteCrawler:
    cls = SITES.get(site) or DISABLED_SITES.get(site)
    if cls is None:
        raise SystemExit(f"지원하지 않는 사이트: {site} (가능: {', '.join(SITES)})")
    if site in DISABLED_SITES:
        log.warning("%s 는 비활성 사이트입니다. 스냅샷 재파싱 용도로만 쓰세요.", site)
    if site in KEYWORD_SITES and keyword:
        return cls(keyword=keyword)
    return cls()


def _preview(value: object, width: int = 90) -> str:
    text = " ".join(str(value).split()) if value is not None else "None"
    return text[:width] + ("…" if len(text) > width else "")


# ── inspect ─────────────────────────────────────────────────────────────────
async def cmd_inspect(args: argparse.Namespace) -> int:
    async with _build_crawler(args.site, getattr(args, "keyword", None)) as crawler:
        jobs, total = await crawler.fetch_list_page(args.page)

        print(f"\n=== {crawler.source} 목록 page={args.page} ===")
        print(f"totalCount = {total}, 이번 페이지 = {len(jobs)}건")
        print(f"원본 저장   = {crawler.snapshot_dir / f'list_p{args.page}.json'}")

        if not jobs:
            print("!! 목록이 비었습니다. 응답 구조를 확인하세요.")
            return 1

        for job in jobs[: args.limit]:
            detailed = await crawler.fetch_detail(job)
            print(f"\n--- {detailed.source_job_id} ---")
            print(f"  url            : {detailed.url}")
            print(f"  title          : {_preview(detailed.title)}")
            print(f"  company_name   : {_preview(detailed.company_name)}")
            print(f"  tech_stacks    : {detailed.tech_stacks}")
            print(f"  job_categories : {detailed.job_categories}")
            print(f"  locations      : {detailed.locations}")
            print(
                f"  career         : min={detailed.career_min} max={detailed.career_max} "
                f"newcomer={detailed.newcomer}"
            )
            print(f"  education      : {detailed.education}")
            print(f"  published_at   : {detailed.published_at}")
            print(f"  closed_at      : {detailed.closed_at}")
            print(f"  company_tags   : {detailed.company_tags}")
            print(f"  company_url    : {detailed.company_url}")
            print(f"  establish      : {detailed.company_establish_date}")
            print(f"  company_src_id : {detailed.company_source_id}")
            print(f"  responsibility : {_preview(detailed.responsibility)}")
            print(f"  qualifications : {_preview(detailed.qualifications)}")
            print(f"  preferred      : {_preview(detailed.preferred_requirements)}")
            print(f"  welfares       : {_preview(detailed.welfares)}")
            print(f"  description len: {len(detailed.build_description() or '')}")
            print(f"  content_hash   : {detailed.content_hash()[:16]}…")
            print(
                f"  원본 저장       : "
                f"{crawler.snapshot_dir / f'position_{detailed.source_job_id}.json'}"
            )

        # 목록 원본의 첫 항목 키를 그대로 보여준다 (필드 추측 방지)
        first_raw = jobs[0].raw.get("list", {})
        print(f"\n=== 목록 원본 필드명 ({len(first_raw)}개) ===")
        print(json.dumps(sorted(first_raw.keys()), ensure_ascii=False))
        print(f"\n요청 수: {crawler.request_count}")
    return 0


# ── crawl ───────────────────────────────────────────────────────────────────
async def cmd_crawl(args: argparse.Namespace) -> int:
    async with _build_crawler(args.site, getattr(args, "keyword", None)) as crawler:
        service = CrawlService(crawler)
        stats = await service.crawl(
            pages=args.pages, with_detail=not args.no_detail, start_page=args.start_page
        )

    print(f"\n=== {args.site} 수집 완료 ===")
    print(f"  페이지        : {stats.pages}/{args.pages}")
    print(f"  사이트 총 건수 : {stats.total_available}")
    print(f"  {stats.as_line()}")
    if stats.error_messages:
        print("  오류:")
        for msg in stats.error_messages[:5]:
            print(f"    - {msg}")

    await dispose_engines()
    return 0 if stats.fetched else 1


# ── reparse ─────────────────────────────────────────────────────────────────
async def cmd_reparse(args: argparse.Namespace) -> int:
    if args.from_snapshots:
        if not args.site:
            raise SystemExit("--from-snapshots 는 --site 가 필요합니다.")
        async with _build_crawler(args.site) as crawler:
            body_stats = await rebuild_bodies_from_snapshots(crawler)
        print("\n=== 스냅샷 본문 재추출 ===")
        print(
            f"  갱신 {body_stats.postings}건 · 추출 실패 {body_stats.without_skills}건 "
            f"· 오류 {body_stats.errors}건"
        )

    stats = await reparse_skills(source=args.site, limit=args.limit)

    print("\n=== 재추출 완료 ===")
    print(f"  {stats.as_line()}")
    if stats.postings:
        print(f"  공고당 평균 스킬 {stats.skills_linked / stats.postings:.1f}개")
    if stats.without_skills:
        print(f"  !! 스킬이 하나도 안 잡힌 공고 {stats.without_skills}건 — 사전 보강 후보")

    await dispose_engines()
    return 0 if stats.errors == 0 else 1


# ── skills report ───────────────────────────────────────────────────────────
async def cmd_skills_report(args: argparse.Namespace) -> int:
    """사이트 태그 중 사전에 없는 값을 빈도순으로 보여준다.

    자동으로 스킬을 만들지 않는다. 사전은 사람이 검토해서 늘린다.
    matched_ratio 는 사전 재현율의 근사치다 — 사이트가 붙인 태그를
    정답으로 보고 그중 몇 %를 사전이 알아보는지 센다.
    """
    async with session_scope() as session:
        unmatched, total, matched = await repository.count_unmatched_tags(session, source=args.site)

    print("\n=== 사전 재현율 (사이트 태그 기준) ===")
    if total == 0:
        print("태그가 있는 공고가 없습니다.")
        await dispose_engines()
        return 0

    print(f"  전체 태그      : {total}")
    print(f"  사전 매칭      : {matched}")
    print(f"  matched_ratio  : {matched / total:.1%}")
    print(f"  미매칭 종류    : {len(unmatched)}")

    if unmatched:
        print(f"\n=== 사전에 없는 태그 상위 {min(args.top, len(unmatched))}개 ===")
        width = max(len(t.tag) for t in unmatched[: args.top])
        for item in unmatched[: args.top]:
            print(f"  {item.tag:<{width}}  {item.count}")
        print("\n검토 후 app/domains/market/seed_data.py 에 추가하고 아래를 실행하세요:")
        print("  uv run python -m scripts.seed_skills && uv run python -m app.cli reparse")

    await dispose_engines()
    return 0


# ── entry ───────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="채용 공고 크롤러")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="실제 응답을 저장하고 파싱 결과를 출력")
    p_inspect.add_argument("--site", required=True, choices=sorted(SITES))
    p_inspect.add_argument("--page", type=int, default=1)
    p_inspect.add_argument("--limit", type=int, default=2, help="상세를 확인할 공고 수")
    p_inspect.add_argument("--keyword", help="검색 키워드 (사람인·잡코리아)")
    p_inspect.set_defaults(func=cmd_inspect)

    p_crawl = sub.add_parser("crawl", help="수집해서 DB 에 적재")
    p_crawl.add_argument("--site", required=True, choices=sorted(SITES))
    p_crawl.add_argument("--pages", type=int, default=1)
    p_crawl.add_argument("--no-detail", action="store_true", help="목록만 수집 (상세 생략)")
    p_crawl.add_argument("--keyword", help="검색 키워드 (사람인·잡코리아)")
    p_crawl.add_argument("--start-page", type=int, default=1, help="이어서 수집할 시작 페이지")
    p_crawl.set_defaults(func=cmd_crawl)

    p_reparse = sub.add_parser("reparse", help="저장된 본문으로 스택만 다시 추출 (재수집 없음)")
    # 비활성 사이트도 스냅샷 재파싱 대상으로는 허용한다.
    p_reparse.add_argument(
        "--site", choices=sorted({*SITES, *DISABLED_SITES}), help="생략하면 전체"
    )
    p_reparse.add_argument("--limit", type=int, help="상위 N건만")
    p_reparse.add_argument(
        "--from-snapshots",
        action="store_true",
        help="data/raw 의 저장된 HTML 로 본문부터 다시 뽑는다 (네트워크 없음)",
    )
    p_reparse.set_defaults(func=cmd_reparse)

    p_skills = sub.add_parser("skills", help="사전 관련 리포트")
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)
    p_report = skills_sub.add_parser("report", help="사전 미매칭 태그 + 재현율")
    p_report.add_argument("--site", choices=sorted(SITES), help="생략하면 전체")
    p_report.add_argument("--top", type=int, default=20)
    p_report.set_defaults(func=cmd_skills_report)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
