# scripts/try_query.py
import asyncio
import dataclasses

from app.core.database import close_engine, get_worker_session
from app.domains.market.queries import ChatQueries


async def test_data_coverage() -> None:
    async with get_worker_session() as session:
        q = ChatQueries(session)

        cov = await q.data_coverage()
        for key, value in dataclasses.asdict(cov).items():
            print(f"{key:<24} {value}")

    await close_engine()


asyncio.run(test_data_coverage())

print("\n\n")


import asyncio

from app.domains.market.enums import CareerLevel, CompanySize, TechField
from app.domains.market.schemas import PopularSkills


def print_popular_skills(title: str, result: PopularSkills) -> None:
    print(f"\n── {title}")
    print(
        f"   대상 공고 {result.analyzed_postings}건 "
        f"(필터 통과 {result.total_postings}건, 최근 {result.days}일)"
    )
    for item in result.items:
        print(f"   {item.rank:>2}. {item.skill:<18} {item.posting_count:>5}건  {item.share:>6.1%}")


async def main() -> None:
    async with get_worker_session() as session:
        q = ChatQueries(session)

        print_popular_skills(
            "백엔드 · 스타트업 · 주니어",
            await q.popular_skills(TechField.BACKEND, CompanySize.STARTUP, CareerLevel.JUNIOR, top=30),
        )
        print_popular_skills("전체", await q.popular_skills(top=30))

    await close_engine()


asyncio.run(main())


print("\n\n")

from app.domains.market.schemas import SkillDemand


def print_skill_demand(query: str, result: SkillDemand | None) -> None:
    if result is None:
        print(f"\n── '{query}' — 사전에 없는 스킬입니다")
        return

    print(f"\n── {result.skill}  (질의: '{query}')")
    print(f"   이 기술을 요구한 공고 {result.total_postings}건")

    def line(label: str, dist: dict[str, int], note: str = "") -> None:
        if not dist:
            print(f"   {label:<6} —")
            return
        body = "  ".join(f"{k} {v}" for k, v in dist.items())
        print(f"   {label:<6} {body}{note}")

    line("등급", result.by_requirement)
    line("분야", result.by_field)
    line("규모", result.by_size)
    # ★ 공고 하나가 여러 구간에 걸치므로(예: "경력 2~5년") 합이 total 보다 크다.
    line("경력", result.by_career, f"   (합 {sum(result.by_career.values())} > total, 구간 중복)")


async def show_skill_demand() -> None:
    async with get_worker_session() as session:
        q = ChatQueries(session)

        for query in ["fastapi", "파이썬", "React", "Spring Boot", "Kubernetes", "없는스킬"]:
            print_skill_demand(query, await q.skill_demand(query))

    await close_engine()


asyncio.run(show_skill_demand())
