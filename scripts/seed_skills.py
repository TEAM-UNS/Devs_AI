"""스킬 사전을 DB 에 심는다.

    python -m scripts.seed_skills          적재
    python -m scripts.seed_skills --check  중복 검사만 (DB 접속 없음)

멱등하다. 여러 번 돌려도 결과가 같다.
카탈로그 원본은 app/domains/market/seed_data.py 다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

from app.core.database import dispose_engines, ensure_selector_event_loop_policy, session_scope
from app.domains.market import repository
from app.domains.market.seed_data import FIELD_CATALOG, SKILL_CATALOG, catalog_stats

ensure_selector_event_loop_policy()


def find_alias_collisions() -> dict[str, list[str]]:
    """두 스킬이 같은 별칭을 주장하면 찾아낸다.

    skill_alias.alias 는 전역 UNIQUE 라 그냥 넣으면 뒤에 온 쪽이 조용히
    버려진다. 사전이 조용히 망가지는 걸 막으려고 미리 검사한다.
    """
    # 같은 스킬이 "Qt"/"QT" 처럼 대소문자만 다른 표기를 둘 다 갖는 건 충돌이 아니다.
    # 소유자를 집합으로 세서 자기 자신과의 충돌을 걸러낸다.
    owners: dict[str, set[str]] = defaultdict(set)
    for skill in SKILL_CATALOG:
        # 대소문자 구분 별칭도 소문자로 비교한다. "C" 와 "c" 가 서로 다른
        # 스킬에 붙으면 사람이 읽기에 사전이 깨진 것이다.
        for alias in (*skill.all_aliases(), *(a.lower() for a in skill.all_cs_aliases())):
            owners[alias].add(skill.name)
    return {alias: sorted(names) for alias, names in owners.items() if len(names) > 1}


def check() -> int:
    collisions = find_alias_collisions()
    stats = catalog_stats()

    print(f"스킬 {len(SKILL_CATALOG)}개 · 분야 {len(FIELD_CATALOG)}개")
    for code, count in stats.items():
        flag = "" if count >= 12 else "  ← 12개 미만"
        print(f"  {code:<10} {count:>3}{flag}")

    ambiguous = [s.name for s in SKILL_CATALOG if s.is_ambiguous]
    common = [s.name for s in SKILL_CATALOG if s.is_common]
    cs = [f"{s.name}({'/'.join(s.all_cs_aliases())})" for s in SKILL_CATALOG if s.cs_aliases]
    print(f"모호 스킬     : {', '.join(ambiguous)}")
    print(f"공통 도구     : {', '.join(common)}")
    print(f"대소문자 구분 : {', '.join(cs)}")
    print(f"별칭 총 {sum(len(s.all_aliases()) + len(s.all_cs_aliases()) for s in SKILL_CATALOG)}개")

    if collisions:
        print("\n!! 별칭 충돌 — 한 별칭을 여러 스킬이 주장합니다:")
        for alias, names in sorted(collisions.items()):
            print(f"   {alias!r}: {', '.join(names)}")
        return 1
    print("별칭 충돌 없음")
    return 0


async def prune() -> int:
    """카탈로그에 없는 스킬을 지운다.

    이전 버전은 사이트 태그를 그대로 skill 로 만들었다("AI/인공지능" 같은 것도).
    그런 행은 category 가 비어 있다 — 카탈로그를 거친 스킬은 항상 category 가
    있으므로 이것으로 구분한다.
    posting_skill 은 FK CASCADE 라 링크도 함께 정리된다. 이후 reparse 로
    사전 기준 링크를 다시 만든다.
    """
    from sqlalchemy import delete, select

    from app.domains.market.models import Skill

    async with session_scope() as session:
        # sqlalchemy 의 select 는 Row 를 돌려준다 (sqlmodel.select 와 다르다)
        rows = (await session.exec(select(Skill.name).where(Skill.category.is_(None)))).all()
        names = sorted(row[0] for row in rows)
        if not names:
            print("정리할 미큐레이션 스킬이 없습니다.")
            return 0
        print(f"카탈로그에 없는 스킬 {len(names)}개를 지웁니다:")
        print("  " + ", ".join(names[:25]) + (" …" if len(names) > 25 else ""))
        await session.exec(delete(Skill).where(Skill.category.is_(None)))

    await dispose_engines()
    return 0


async def seed() -> int:
    if (code := check()) != 0:
        print("\n충돌을 먼저 해결하세요. 적재하지 않았습니다.")
        return code

    async with session_scope() as session:
        field_ids = await repository.upsert_tech_fields(
            session, [(f.code, f.name, f.sort_order) for f in FIELD_CATALOG]
        )

        alias_total = 0
        for skill in SKILL_CATALOG:
            skill_id = await repository.upsert_skill(
                session,
                name=skill.name,
                category=skill.category,
                is_ambiguous=skill.is_ambiguous,
                is_common=skill.is_common,
            )
            # 정규 표기는 매처가 자동으로 붙이므로 별칭 테이블에는 넣지 않는다.
            # 단 대소문자 구분 표기는 플래그를 실어야 하므로 반드시 넣는다.
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
        f"\n적재 완료: 분야 {len(field_ids)}개 · 스킬 {len(SKILL_CATALOG)}개 · 별칭 {alias_total}개"
    )
    await dispose_engines()
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
