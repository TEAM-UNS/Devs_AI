"""market.queries 를 실 데이터로 하나씩 돌려 본다.

    uv run python -m scripts.try_queries            전부
    uv run python -m scripts.try_queries salary     이름으로 골라서
    uv run python -m scripts.try_queries --list     목록만

쿼리를 새로 만들면 여기에 데모를 한 줄 추가한다. 숫자가 말이 되는지
눈으로 확인하는 것이 목적이라 서식을 사람이 읽기 좋게 맞춘다.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

from app.core.database import close_engine, get_worker_session
from app.domains.market.enums import CareerLevel, CompanySize
from app.domains.market.enums import TechField as TF
from app.domains.market.queries import ChatQueries
from app.domains.market.schemas import (
    CompanyComparisons,
    CompanyLookup,
    DataCoverage,
    PopularSkills,
    RelatedSkills,
    RisingSkills,
    SalaryStats,
    SkillCount,
    SkillDemand,
    SkillGap,
    StacksBySegment,
)


def title(text: str) -> None:
    print(f"\n{'─' * 72}\n── {text}\n")


def skill_lines(items: list[SkillCount], indent: str = "   ") -> None:
    for item in items:
        share = f"{item.share:>6.1%}" if item.share else ""
        print(f"{indent}{item.rank:>2}. {item.skill:<20} {item.posting_count:>5}건 {share}")


# ── 데모 ────────────────────────────────────────────────────────────────────
def show_coverage(result: DataCoverage) -> None:
    print(f"   수집 기간   {result.collected_from} ~ {result.collected_to}")
    print(f"   공고        {result.total_postings}건 (유효 {result.active_postings})")
    print(f"   기업        {result.company_count}곳")
    print(f"   본문이 이미지만  {result.image_only_ratio:.1%}")
    print(f"   급여 공개율      {result.salary_disclosure_rate:.1%}")
    print(f"   미분류 공고      {result.unclassified_postings}건")
    print(f"   분야별      {result.by_field}")
    print(f"   등급별      {result.requirement_breakdown}")
    print(f"   최종 수집   {result.last_crawl_at}")


def show_popular(result: PopularSkills, label: str) -> None:
    print(f"   [{label}] 대상 {result.analyzed_postings}건 / 필터통과 {result.total_postings}건")
    skill_lines(result.items)


def show_rising(result: RisingSkills) -> None:
    print(
        f"   최근 {result.window_days}일 {result.recent_postings}건 "
        f"vs 직전 {result.previous_postings}건"
        + ("   ★ 표본이 얇다" if result.low_confidence else "")
    )
    for item in result.items:
        print(
            f"   {item.rank:>2}. {item.skill:<20} "
            f"{item.previous_count:>4} → {item.recent_count:<4} {item.growth_rate:+.1%}"
        )


def show_segments(result: StacksBySegment) -> None:
    for segment in result.segments:
        skills = ", ".join(f"{s.skill}({s.posting_count})" for s in segment.skills)
        print(f"   [{segment.segment:<12}] {skills}")


def show_salary(result: SalaryStats, label: str) -> None:
    flag = "   ★ 표본 부족" if result.low_confidence else ""
    print(
        f"   [{label}] 표본 {result.sample_size}/{result.total_postings}건 "
        f"· 공개율 {result.disclosure_rate:.1%}{flag}"
    )
    if result.median is None:
        print("   집계할 급여 정보가 없습니다.")
        return
    print(
        f"   중앙값 {result.median}{result.unit}   "
        f"Q1 {result.q1} / Q3 {result.q3}   범위 {result.minimum}~{result.maximum}"
    )
    print(f"   원문 유형  {result.breakdown}")


def show_related(result: RelatedSkills | None, query: str) -> None:
    if result is None:
        print(f"   '{query}' — 사전에 없는 스킬입니다")
        return
    print(f"   {result.skill} 이(가) 나온 공고 {result.base_postings}건과 함께 등장")
    for item in result.items:
        print(f"   {item.rank:>2}. {item.skill:<20} 동시 {item.cooccurrence:>4}건  NPMI {item.npmi:+.3f}")


def show_demand(result: SkillDemand | None, query: str) -> None:
    if result is None:
        print(f"   '{query}' — 사전에 없는 스킬입니다")
        return
    print(f"   {result.skill} 을(를) 요구한 공고 {result.total_postings}건")
    print(f"   등급  {result.by_requirement}")
    print(f"   분야  {result.by_field}")
    print(f"   규모  {result.by_size}")
    print(f"   경력  {result.by_career}   (구간이 겹쳐 합이 total 보다 크다)")


def show_company(result: CompanyLookup, query: str) -> None:
    if result.status == "not_found":
        print(f"   '{query}' — 찾지 못했습니다")
        return
    if result.status == "ambiguous":
        print(f"   '{query}' — 후보가 여럿입니다. 되물어야 합니다:")
        for candidate in result.candidates[:8]:
            print(f"      #{candidate.company_id} {candidate.name} ({candidate.posting_count}건)")
        return

    profile = result.profile
    assert profile is not None
    print(f"   {profile.name}  (#{profile.company_id}, {profile.size_type}, 공고 {profile.posting_count}건)")
    print(f"   업종 {profile.industry} · 설립 {profile.founded} · 사원 {profile.employee_count}")
    print(f"   경력 {profile.career_distribution}")
    print(f"   지역 {profile.locations}")
    if profile.talent_profile:
        print(f"   인재상 {profile.talent_profile[:60]}...")
    print("   요구 스택:")
    skill_lines(profile.top_skills, indent="      ")


def show_comparison(result: CompanyComparisons) -> None:
    for company in result.companies:
        skills = ", ".join(s.skill for s in company.top_skills[:6])
        print(f"   {company.name:<20} ({company.size_type}, {company.posting_count}건) {skills}")
    print(f"   공통 스택: {result.shared_skills or '없음'}")


def show_gap(result: SkillGap) -> None:
    print(f"   인식 {result.matched} / 미인식 {result.unknown_inputs}")
    print(f"   대상 공고 {result.analyzed_postings}건 · 커버리지 {result.coverage:.1%}")
    print("   부족한 기술:")
    for item in result.missing:
        print(f"      {item.rank:>2}. {item.skill:<20} {item.posting_count:>5}건 {item.share:>6.1%}")


# ── 실행 ────────────────────────────────────────────────────────────────────
async def demo_coverage(q: ChatQueries) -> None:
    title("data_coverage — 수집 현황 (신뢰도 질문 전 선행 호출용)")
    show_coverage(await q.data_coverage())


async def demo_popular(q: ChatQueries) -> None:
    title("popular_skills — 조건에 맞는 공고가 요구하는 기술 순위")
    show_popular(await q.popular_skills(top=10), "전체 · 30일")
    print()
    show_popular(await q.popular_skills(TF.BACKEND, top=8), "백엔드")
    print()
    show_popular(
        await q.popular_skills(TF.FRONTEND, CompanySize.STARTUP, CareerLevel.JUNIOR, top=8),
        "프론트 · 스타트업 · 주니어",
    )


async def demo_rising(q: ChatQueries) -> None:
    title("rising_skills — 이번 N일 vs 직전 N일 증감률")
    show_rising(await q.rising_skills(top=10))


async def demo_segments(q: ChatQueries) -> None:
    title("stacks_by_segment — 세그먼트별 상위 스택")
    for group_by in ("size", "career", "location"):
        print(f"   ▸ group_by={group_by}")
        show_segments(await q.stacks_by_segment(group_by, top=5))
        print()


async def demo_salary(q: ChatQueries) -> None:
    title("salary_stats — 중앙값·사분위 + 공개율")
    show_salary(await q.salary_stats(), "전체")
    print()
    show_salary(await q.salary_stats(TF.BACKEND), "백엔드")
    print()
    show_salary(await q.salary_stats(skill="Python"), "Python 요구")


async def demo_related(q: ChatQueries) -> None:
    title("related_skills — 함께 요구되는 기술 (NPMI)")
    for name in ("React", "Kubernetes", "없는스킬"):
        show_related(await q.related_skills(name, top=8), name)
        print()


async def demo_demand(q: ChatQueries) -> None:
    title("skill_demand — 이 기술을 어디서 요구하나")
    for name in ("Python", "React", "없는스킬"):
        show_demand(await q.skill_demand(name), name)
        print()


async def demo_company(q: ChatQueries) -> None:
    title("company_profile — 기업 프로필 (동명이면 후보 목록)")
    for name in ("텐빌리언", "카카오", "존재하지않는회사"):
        show_company(await q.company_profile(name), name)
        print()


async def demo_compare(q: ChatQueries) -> None:
    title("compare_companies — 기업 간 요구 스택 비교")
    lookups = [await q.company_profile(name) for name in ("텐빌리언", "쿤텍", "블루개러지")]
    ids = [lookup.profile.company_id for lookup in lookups if lookup.profile]
    if len(ids) < 2:
        print("   비교할 기업을 찾지 못했습니다.")
        return
    show_comparison(await q.compare_companies(ids, top=8))


async def demo_gap(q: ChatQueries) -> None:
    title("skill_gap — 내 스킬과 시장 요구의 차이")
    show_gap(await q.skill_gap(["파이썬", "Docker", "없는스킬"], field=TF.BACKEND, top=8))


DEMOS: dict[str, Callable[[ChatQueries], Awaitable[None]]] = {
    "coverage": demo_coverage,
    "popular": demo_popular,
    "rising": demo_rising,
    "segments": demo_segments,
    "salary": demo_salary,
    "related": demo_related,
    "demand": demo_demand,
    "company": demo_company,
    "compare": demo_compare,
    "gap": demo_gap,
}


async def main(names: list[str]) -> None:
    async with get_worker_session() as session:
        q = ChatQueries(session)
        for name in names:
            await DEMOS[name](q)
    await close_engine()


args = [a for a in sys.argv[1:] if not a.startswith("-")]
if "--list" in sys.argv:
    print("사용 가능한 데모:", ", ".join(DEMOS))
    raise SystemExit(0)

unknown = [a for a in args if a not in DEMOS]
if unknown:
    print(f"모르는 데모: {unknown}   (가능: {', '.join(DEMOS)})")
    raise SystemExit(1)

asyncio.run(main(args or list(DEMOS)))
