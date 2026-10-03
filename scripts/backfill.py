# 과거 공고 백필 — 등록일 기준으로 N일 전까지 거슬러 받는다
"""지난 N일치 공고를 메운다.

    uv run python -m scripts.backfill --days 30                # 전 사이트
    uv run python -m scripts.backfill --days 30 --site saramin # 한 사이트만
    uv run python -m scripts.backfill --days 7  --no-detail    # 목록만 (얼마나 걸릴지 가늠용)

평소 수집(app.cli crawl · arq)과 끊는 기준이 다르다.

    평소   기수집 공고만 나오는 페이지가 연속 → 중단
           목적이 "새로 올라온 것 줍기" 라, 아는 구간을 만나면 더 볼 게 없다.
    백필   등록일이 기준일보다 과거로 내려감 → 중단
           목적이 "빈 과거 구간 메우기" 라, 아는 구간을 만나도 지나쳐야 한다.
           기수집 기준으로 끊으면 그 너머를 영영 못 채운다.

★ 이미 가진 공고는 상세를 다시 받지 않는다 (--skip-seen-days 기본 7일).
  목록만 훑고 지나가므로 중복 구간은 페이지당 요청 1번으로 끝난다.

★ 마감된 공고는 목록에 안 뜬다.
  사이트가 활성 공고만 노출하므로, 과거로 갈수록 "그때 올라와서 아직 열려
  있는 공고" 만 받힌다. 기간별 건수 추이를 볼 때 이 점을 감안해야 한다.

★ 느리다. 사람인은 공고 1건당 요청 2번(조건 ajax + 본문)이다.
  한 번에 다 돌리지 말고 --site 로 나눠 돌리는 쪽을 권한다. 403 이 반복되면
  CRAWL_DELAY_SECONDS 를 올린다 (기본 1.0).
"""

import argparse
import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from app.core.database import close_engine
from app.domains.crawler.config import build_crawler, iter_crawl_jobs
from app.domains.crawler.service import CrawlService

log = logging.getLogger("backfill")

# 평소 상한(60~80)으로는 한 달을 못 내려간다. 백필에서만 크게 잡는다.
MAX_PAGES = 400


def _targets(site: str | None) -> list[tuple[str, str | None]]:
    """(사이트, 키워드) 조합. iter_crawl_jobs 와 같은 설정을 쓴다."""
    jobs = [(job.site, job.keyword) for job in iter_crawl_jobs()]
    return [j for j in jobs if site is None or j[0] == site]


async def _one(
    site: str,
    keyword: str | None,
    cutoff: datetime,
    *,
    with_detail: bool,
    skip_seen_days: int,
    max_pages: int,
) -> None:
    label = f"{site}/{keyword or '전체'}"
    started = time.monotonic()
    print(f"\n── {label} 시작")

    async with build_crawler(site, keyword) as crawler:
        stats = await CrawlService(crawler).crawl(
            pages=max_pages,
            with_detail=with_detail,
            skip_seen_days=skip_seen_days,
            keyword=keyword,
            until_posted_before=cutoff,
        )

    elapsed = time.monotonic() - started
    print(f"   {stats.as_line()}")
    print(f"   페이지 {stats.pages} · 요청 {crawler.request_count} · {elapsed:.0f}초")
    if stats.error_messages:
        for message in stats.error_messages[:3]:
            print(f"   ! {message}")


async def main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    targets = _targets(args.site)
    if not targets:
        raise SystemExit(f"대상이 없습니다: site={args.site}")

    print(f"\n=== 백필 시작 — {cutoff.date()} 이후 등록 공고 ===")
    print(f"  대상 {len(targets)}개 · 상세 {'생략' if args.no_detail else '수집'}")

    started = time.monotonic()
    for site, keyword in targets:
        try:
            await _one(
                site,
                keyword,
                cutoff,
                with_detail=not args.no_detail,
                skip_seen_days=args.skip_seen_days,
                max_pages=args.max_pages,
            )
        except Exception:
            # 한 조합이 막혀도 나머지는 계속한다
            log.exception("%s/%s 실패 — 다음 대상으로", site, keyword)
        if args.rest and (site, keyword) != targets[-1]:
            print(f"   ({args.rest}초 쉼)")
            await asyncio.sleep(args.rest)

    print(f"\n=== 백필 완료 — {time.monotonic() - started:.0f}초 ===")
    print("  임베딩은 따로 돌립니다: uv run python -m app.cli embed")
    await close_engine()
    return 0


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--days", type=int, default=30, help="며칠 전까지 거슬러 받을지 (기본 30)")
parser.add_argument("--site", help="한 사이트만 (생략하면 수집 설정의 전 사이트)")
parser.add_argument(
    "--no-detail",
    action="store_true",
    help="상세를 받지 않는다. ★ 읽기 전용이 아니다 — 목록에서 얻은 값으로 공고 행은 "
    "그대로 적재된다(본문·스킬 없이). 깊이와 소요 시간을 가늠할 때 쓴다.",
)
parser.add_argument(
    "--skip-seen-days", type=int, default=7, help="최근 N일 내 수집분은 상세 생략 (0=끄기)"
)
parser.add_argument("--max-pages", type=int, default=MAX_PAGES, help="안전 상한")
parser.add_argument("--rest", type=int, default=30, help="대상 사이에 쉬는 초 (기본 30)")

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parser.parse_args())))
