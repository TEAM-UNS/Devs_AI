# 스킬 사전 DB 적재

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

from app.core.database import close_engine, get_worker_session
from app.domains.crawler.seed_data import catalog_stats, field_catalog, skill_catalog
from app.domains.crawler import repository


def find_alias_collisions() -> dict[str, list[str]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for skill in skill_catalog():
        # 대소문자 구분 별칭도 소문자로 비교한다. 다른 스킬의 "C" 와 "c" 도 충돌이다
        for alias in (*skill.all_aliases(), *(a.lower() for a in skill.all_cs_aliases())):
            owners[alias].add(skill.name)
    return {alias: sorted(names) for alias, names in owners.items() if len(names) > 1}


def check() -> int:
    collisions = find_alias_collisions()
    stats = catalog_stats()

    print(f"스킬 {len(skill_catalog())}개 · 분야 {len(field_catalog())}개")
    for code, count in stats.items():
        flag = "" if count >= 12 else "  ← 12개 미만"
        print(f"  {code:<10} {count:>3}{flag}")

    ambiguous = [s.name for s in skill_catalog() if s.is_ambiguous]
    common = [s.name for s in skill_catalog() if s.is_common]
    cs = [f"{s.name}({'/'.join(s.all_cs_aliases())})" for s in skill_catalog() if s.cs_aliases]
    print(f"모호 스킬     : {', '.join(ambiguous)}")
    print(f"공통 도구     : {', '.join(common)}")
    print(f"대소문자 구분 : {', '.join(cs)}")
    print(f"별칭 총 {sum(len(s.all_aliases()) + len(s.all_cs_aliases()) for s in skill_catalog())}개")

    if collisions:
        print("\n!! 별칭 충돌 — 한 별칭을 여러 스킬이 주장합니다:")
        for alias, names in sorted(collisions.items()):
            print(f"   {alias!r}: {', '.join(names)}")
        return 1
    print("별칭 충돌 없음")
    return 0


async def prune() -> int:
    from sqlalchemy import delete, select

    from app.domains.crawler.models import Skill

    async with get_worker_session() as session:
        # 카탈로그를 거친 스킬은 항상 category 가 있다. 비어 있으면 옛 사이트 태그 스킬이다
        rows = (await session.exec(select(Skill.name).where(Skill.category.is_(None)))).all()
        names = sorted(row[0] for row in rows)
        if not names:
            print("정리할 미큐레이션 스킬이 없습니다.")
            return 0
        print(f"카탈로그에 없는 스킬 {len(names)}개를 지웁니다:")
        print("  " + ", ".join(names[:25]) + (" …" if len(names) > 25 else ""))
        await session.exec(delete(Skill).where(Skill.category.is_(None)))

    await close_engine()
    return 0


async def seed() -> int:
    if (code := check()) != 0:
        print("\n충돌을 먼저 해결하세요. 적재하지 않았습니다.")
        return code

    async with get_worker_session() as session:
        field_ids = await repository.upsert_tech_fields(
            session, [(f.code, f.name, f.sort_order) for f in field_catalog()]
        )

        alias_total = 0
        for skill in skill_catalog():
            skill_id = await repository.upsert_skill(
                session,
                name=skill.name,
                category=skill.category,
                is_ambiguous=skill.is_ambiguous,
                is_common=skill.is_common,
            )
            # 정규 표기는 매처가 자동으로 붙이므로 별칭 테이블에 넣지 않는다
            aliases = [a for a in skill.all_aliases() if a != skill.name.lower()]
            alias_total += await repository.replace_skill_aliases(
                session, skill_id, aliases, skill.all_cs_aliases()
            )
            await repository.replace_skill_fields(
                session,
                skill_id,
                [field_ids[f.value] for f in skill.fields if f.value in field_ids],
            )

    print(
        f"\n적재 완료: 분야 {len(field_ids)}개 · 스킬 {len(skill_catalog())}개 · 별칭 {alias_total}개"
    )
    await close_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="scripts.seed_skills")
    parser.add_argument("--check", action="store_true", help="DB 접속 없이 사전만 검사")
    parser.add_argument("--prune", action="store_true", help="카탈로그에 없는 스킬 삭제")
    args = parser.parse_args()
    if args.check:
        return check()
    if args.prune:
        return asyncio.run(prune())
    return asyncio.run(seed())


if __name__ == "__main__":
    sys.exit(main())
