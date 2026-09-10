"""크롤러 · 임베딩 CLI.

    python -m app.cli inspect --site jumpit [--page 1] [--limit 2]
    python -m app.cli crawl   --site jumpit --pages 2 [--no-detail]
                              [--skip-seen-days 7] [--force-reextract]
    python -m app.cli embed   [--limit 500] [--fake] [--companies]
    python -m app.cli vector-index --build | --drop

inspect 는 DB 를 건드리지 않는다. 실제 응답을 data/raw/ 에 저장하고
파서가 뽑아낸 값을 사람이 눈으로 확인하는 용도다.

embed 는 arq 없이 임베딩 파이프라인만 돌린다 (태스크와 같은 서비스 함수를
부른다). 워커를 띄우지 않고 결과를 확인할 때 쓴다.
"""

import argparse
import asyncio
import json
import logging
import sys
import time

from sqlalchemy import text as sa_text

from app.core.database import close_engine, get_worker_session, session_factory
from app.domains.crawler import embed_service
from app.domains.crawler.config import DEFAULT_SKIP_SEEN_DAYS
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
from app.domains.market import repository, vector_index
from app.llm.embed_adapter import build_embedder

log = logging.getLogger(__name__)

SITES: dict[str, type[BaseSiteCrawler]] = {
    "jumpit": JumpitCrawler,
    "wanted": WantedCrawler,
    "saramin": SaraminCrawler,
}
KEYWORD_SITES = {"saramin", "jobkorea"}

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

        first_raw = jobs[0].raw.get("list", {})
        print(f"\n=== 목록 원본 필드명 ({len(first_raw)}개) ===")
        print(json.dumps(sorted(first_raw.keys()), ensure_ascii=False))
        print(f"\n요청 수: {crawler.request_count}")
    return 0


# ── crawl ───────────────────────────────────────────────────────────────────
async def cmd_crawl(args: argparse.Namespace) -> int:
    started = time.monotonic()
    keyword = getattr(args, "keyword", None)
    async with _build_crawler(args.site, keyword) as crawler:
        service = CrawlService(crawler, force_reextract=args.force_reextract)
        stats = await service.crawl(
            pages=args.pages,
            with_detail=not args.no_detail,
            start_page=args.start_page,
            skip_seen_days=args.skip_seen_days,
            keyword=keyword,
        )
    elapsed = time.monotonic() - started

    print(f"\n=== {args.site} 수집 완료 ===")
    print(f"  페이지        : {stats.pages}/{args.pages}")
    print(f"  사이트 총 건수 : {stats.total_available}")
    print(f"  {stats.as_line()}")
    print(f"  요청 수        : {crawler.request_count}")
    print(f"  소요           : {elapsed:.1f}초")
    if stats.error_messages:
        print("  오류:")
        for msg in stats.error_messages[:5]:
            print(f"    - {msg}")

    await close_engine()
    return 0 if stats.fetched else 1


# ── embed ───────────────────────────────────────────────────────────────────
async def cmd_embed(args: argparse.Namespace) -> int:
    embedder = build_embedder(force_fake=args.fake)
    factory = session_factory
    started = time.monotonic()

    print(f"\n=== 임베딩 시작 (embedder={type(embedder).__name__}) ===")

    if args.companies:
        stats = await embed_service.embed_companies(factory, embedder, limit=args.limit)
    else:
        ids = [int(v) for v in args.ids] if args.ids else None
        stats = await embed_service.embed_postings(
            factory, embedder, posting_ids=ids, limit=None if ids else args.limit
        )

    print(f"  {stats.as_line()}")
    print(f"  기업          : {stats.companies}")
    print(f"  소요          : {time.monotonic() - started:.1f}초")
    if stats.error_messages:
        print("  오류:")
        for msg in stats.error_messages[:5]:
            print(f"    - {msg}")

    async with get_worker_session() as session:
        remaining = await repository.count_postings_to_embed(session)
        by_section = await repository.count_chunks_by_section(session)

    print(f"\n  남은 대상     : {remaining}건")
    print("  섹션별 청크   :")
    for section, count in by_section:
        print(f"    {section:<16} {count}")

    await close_engine()
    return 0 if stats.errors == 0 else 1


# ── vector-index ────────────────────────────────────────────────────────────
async def cmd_vector_index(args: argparse.Namespace) -> int:
    action = "DROP" if args.drop else "BUILD"
    print(f"\n=== HNSW 인덱스 {action} ===")

    async with get_worker_session() as session:
        if not args.drop:
            for setup in vector_index.BUILD_SESSION_SETUP:
                await session.exec(sa_text(setup))

        for name, ddl in vector_index.INDEXES:
            if args.drop:
                print(f"  drop {name} …")
                await session.exec(sa_text(f"DROP INDEX IF EXISTS market.{name}"))
                continue
            print(f"  create {name} … (데이터가 많으면 수 분 걸린다)")
            await session.exec(sa_text(ddl))

        rows = (
            await session.exec(
                sa_text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname='market' AND indexdef ILIKE '%hnsw%' ORDER BY 1"
                )
            )
        ).all()

    print(f"\n  현재 HNSW 인덱스: {[r[0] for r in rows] or '(없음)'}")
    await close_engine()
    return 0


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

    await close_engine()
    return 0 if stats.errors == 0 else 1


# ── skills report ───────────────────────────────────────────────────────────
async def cmd_skills_report(args: argparse.Namespace) -> int:
    async with get_worker_session() as session:
        unmatched, total, matched = await repository.count_unmatched_tags(session, source=args.site)

    print("\n=== 사전 재현율 (사이트 태그 기준) ===")
    if total == 0:
        print("태그가 있는 공고가 없습니다.")
        await close_engine()
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

    # ★ 태그만 보면 최근 기술을 놓친다. 사이트 태그 목록에 아직 없고 본문에만
    #   적히기 때문이다. 실제로 RAG·pgvector 같은 벡터 스택이 통째로 빠져
    #   있었는데 태그 리포트에는 한 건도 안 떴다.
    async with get_worker_session() as session:
        body_terms = await repository.count_unmatched_body_terms(
            session, source=args.site, min_count=args.min_count, limit=args.top
        )

    if body_terms:
        print(f"\n=== 본문에 자주 나오는데 사전에 없는 말 상위 {len(body_terms)}개 ===")
        print("    (일반 단어가 섞인다. 기술인지 사람이 판단할 것)")
        width = max(len(t.tag) for t in body_terms)
        for item in body_terms:
            print(f"  {item.tag:<{width}}  {item.count}")

    if unmatched or body_terms:
        print("\n검토 후 app/domains/market/seed_data.py 에 추가하고 아래를 실행하세요:")
        print("  uv run python -m scripts.seed_skills && uv run python -m app.cli reparse")

    await close_engine()
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
    p_crawl.add_argument(
        "--skip-seen-days",
        type=int,
        default=DEFAULT_SKIP_SEEN_DAYS,
        help="최근 N일 내 수집한 공고는 목록에서 걸러 상세를 받지 않는다 (0=끄기)",
    )
    p_crawl.add_argument(
        "--force-reextract",
        action="store_true",
        help="content_hash 가 같아도 다시 적재한다 (파서 수정 후 백필용)",
    )
    p_crawl.set_defaults(func=cmd_crawl)

    p_embed = sub.add_parser("embed", help="임베딩 실행 (arq 없이)")
    p_embed.add_argument("--limit", type=int, help="상위 N건만 (생략 시 전량)")
    p_embed.add_argument("--ids", nargs="+", help="특정 posting_id 만")
    p_embed.add_argument("--companies", action="store_true", help="기업 프로필 임베딩")
    p_embed.add_argument("--fake", action="store_true", help="FakeEmbedder 사용 (API 키 불필요)")
    p_embed.set_defaults(func=cmd_embed)

    p_index = sub.add_parser("vector-index", help="HNSW 인덱스 생성/삭제 (적재 후 실행)")
    p_index.add_argument("--drop", action="store_true", help="생성 대신 삭제")
    p_index.set_defaults(func=cmd_vector_index)

    p_reparse = sub.add_parser("reparse", help="저장된 본문으로 스택만 다시 추출 (재수집 없음)")
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
    p_report.add_argument(
        "--min-count", type=int, default=30, help="본문 스캔에서 이 횟수 미만은 버린다"
    )
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
