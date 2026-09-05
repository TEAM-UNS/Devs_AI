"""★ 읽기 전용 — 챗봇 툴의 유일한 DB 진입점 (R3).

ORM 엔티티가 아니라 schemas.py 의 DTO 를 반환한다.
집계는 여기서 SQL 로 끝낸다. 툴 함수는 조립만 한다.

트렌드
    popular_skills(field, size_type, career_level, days, top)
    rising_skills(field, min_count, top)      2주 구간 비교 + (this+1)/(last+1)-1
    stacks_by_segment(group_by, field, top)   size · career · location
    salary_stats(...)                         중앙값/사분위 + disclosure_rate
                                              (salary_type != negotiable 만 집계,
                                               분모는 전체 공고수)
기술 관계
    related_skills(skill, field, requirement, top)   동시출현 + NPMI
    skill_demand(skill)                              분야·규모·경력 분포
    resolve_skill(query)                             skill.embedding 코사인

기업
    company_profile(name)          동명 다수면 후보 목록 반환
    similar_companies(company_id)  similarity.py 에 위임
    compare_companies(ids)
    search_companies(query_vec, filters)   pgvector + 메타 필터 한 쿼리

탐색·메타
    search_postings(query_vec, filters, section)  posting_chunk 벡터 검색
    skill_gap(my_skills, field, company_ids)      requirement in (required, tag)
    data_coverage()                               수집 기간 · 총 공고수 · 최종 수집시각

주의: 표본이 작은 결과에는 sample_size 를 반드시 함께 실어 보낸다.
"""

from __future__ import annotations

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.market.models import (
    JobPosting,
)
from app.domains.market.schemas import (
    DataCoverage,
)

# ── 공용 표현식 ─────────────────────────────────────────────────────────────
# ★ collected_at 을 쓰면 안 된다. 재수집 때마다 갱신되는 "마지막으로 본 시각"
APPEARED_AT = func.coalesce(JobPosting.posted_at, JobPosting.created_at)


async def data_coverage(
    session: AsyncSession,
) -> DataCoverage:
    stmt = select()
