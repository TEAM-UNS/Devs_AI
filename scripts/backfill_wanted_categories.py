"""원티드 공고의 스택 태그·직무 카테고리를 저장된 스냅샷에서 복구한다.

    uv run python -m scripts.backfill_wanted_categories [--dry-run]

★ 일회성 백필이다. 두 파서 버그로 원티드 공고 전량이 태그·카테고리 없이
  적재됐다.
    - _titles 가 "text" 키를 몰라 skill_tags 를 통째로 버렸다 (tags_raw 가 빈 배열)
    - category_tag.child_tags 를 아예 읽지 않았다 (job_categories 가 빈 배열)
  둘 다 고쳤으므로 앞으로 수집분은 정상이다. 이 스크립트는 이미 쌓인 것만
  data/raw/wanted/position_*.json 에서 되살린다. 사이트에 재요청하지 않는다.

이 스크립트 뒤에 반드시 재파싱을 돌려야 스킬·분야가 반영된다:
    uv run python -m app.cli reparse --site wanted
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text as sa_text

from app.core.config import BASE_DIR
from app.core.database import close_engine, get_worker_session
from app.domains.crawler.sites.wanted import _job_categories, _titles

SNAPSHOT_DIR = BASE_DIR / "data" / "raw" / "wanted"


def read_snapshot(path: Path) -> tuple[str, list[str], list[str]] | None:
    """스냅샷 1건 → (source_job_id, 스킬태그, 직무카테고리)."""
    try:
        posting = json.loads(path.read_text(encoding="utf-8")).get("job") or {}
    except (json.JSONDecodeError, OSError):
        return None
    if not posting.get("id"):
        return None
    return (
        str(posting["id"]),
        _titles(posting.get("skill_tags")),
        _job_categories(posting.get("category_tag")),
    )


async def main(dry_run: bool) -> int:
    paths = sorted(SNAPSHOT_DIR.glob("position_*.json"))
    print(f"스냅샷 {len(paths)}건")

    rows = [row for path in paths if (row := read_snapshot(path))]
    with_tags = sum(1 for _, tags, _ in rows if tags)
    with_cats = sum(1 for _, _, cats in rows if cats)
    print(f"  파싱 성공 {len(rows)}건 · 스택태그 {with_tags}건 · 카테고리 {with_cats}건")

    if dry_run:
        print("\n--dry-run 이라 DB 를 건드리지 않습니다. 샘플 3건:")
        for job_id, tags, cats in rows[:3]:
            print(f"  {job_id}  tags={tags[:5]}  cats={cats}")
        return 0

    updated = 0
    async with get_worker_session() as session:
        for job_id, tags, cats in rows:
            if not tags and not cats:
                continue
            result = await session.exec(
                sa_text(
                    """
                    UPDATE market.job_posting
                    SET tags_raw   = CAST(:tags AS jsonb),
                        raw_fields = raw_fields || jsonb_build_object(
                            'job_categories', CAST(:cats AS jsonb)
                        )
                    WHERE source = 'wanted' AND source_job_id = :job_id
                    """
                ).bindparams(
                    tags=json.dumps(tags, ensure_ascii=False),
                    cats=json.dumps(cats, ensure_ascii=False),
                    job_id=job_id,
                )
            )
            updated += result.rowcount or 0

    print(f"\n갱신 {updated}건")
    print("이제 재파싱하세요:  uv run python -m app.cli reparse --site wanted")
    await close_engine()
    return 0


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dry-run", action="store_true", help="DB 를 건드리지 않고 결과만 본다")
raise SystemExit(asyncio.run(main(parser.parse_args().dry_run)))
