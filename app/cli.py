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

from app.core.database import dispose_engines, ensure_selector_event_loop_policy
from app.domains.crawler.service import CrawlService, reparse_skills
from app.domains.crawler.sites.base import BaseSiteCrawler
from app.domains.crawler.sites.jumpit import JumpitCrawler

# Windows: psycopg async 는 SelectorEventLoop 를 요구한다. 루프 생성 전에 호출.
ensure_selector_event_loop_policy()

SITES: dict[str, type[BaseSiteCrawler]] = {
    "jumpit": JumpitCrawler,
}


def _build_crawler(site: str) -> BaseSiteCrawler:
    try:
        return SITES[site]()
    except KeyError:
        raise SystemExit(f"지원하지 않는 사이트: {site} (가능: {', '.join(SITES)})") from None


def _preview(value: object, width: int = 90) -> str:
    text = " ".join(str(value).split()) if value is not None else "None"
    return text[:width] + ("…" if len(text) > width else "")


# ── inspect ─────────────────────────────────────────────────────────────────
async def cmd_inspect(args: argparse.Namespace) -> int:
    async with _build_crawler(args.site) as crawler:
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
    async with _build_crawler(args.site) as crawler:
        service = CrawlService(crawler)
        stats = await service.crawl(pages=args.pages, with_detail=not args.no_detail)

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
    stats = await reparse_skills(source=args.site, limit=args.limit)

    print("\n=== 재추출 완료 ===")
    print(f"  {stats.as_line()}")
    if stats.postings:
        print(f"  공고당 평균 스킬 {stats.skills_linked / stats.postings:.1f}개")
    if stats.without_skills:
        print(f"  !! 스킬이 하나도 안 잡힌 공고 {stats.without_skills}건 — 사전 보강 후보")

    await dispose_engines()
    return 0 if stats.errors == 0 else 1


# ── entry ───────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="채용 공고 크롤러")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="실제 응답을 저장하고 파싱 결과를 출력")
    p_inspect.add_argument("--site", required=True, choices=sorted(SITES))
    p_inspect.add_argument("--page", type=int, default=1)
    p_inspect.add_argument("--limit", type=int, default=2, help="상세를 확인할 공고 수")
    p_inspect.set_defaults(func=cmd_inspect)

    p_crawl = sub.add_parser("crawl", help="수집해서 DB 에 적재")
    p_crawl.add_argument("--site", required=True, choices=sorted(SITES))
    p_crawl.add_argument("--pages", type=int, default=1)
    p_crawl.add_argument("--no-detail", action="store_true", help="목록만 수집 (상세 생략)")
    p_crawl.set_defaults(func=cmd_crawl)

    p_reparse = sub.add_parser("reparse", help="저장된 본문으로 스택만 다시 추출 (재수집 없음)")
    p_reparse.add_argument("--site", choices=sorted(SITES), help="생략하면 전체")
    p_reparse.add_argument("--limit", type=int, help="상위 N건만")
    p_reparse.set_defaults(func=cmd_reparse)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
