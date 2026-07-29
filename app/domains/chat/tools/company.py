"""기업 툴 — 프로필 · 유사 · 비교 · 검색. → market.queries

get_company_profile(name)
    기본정보 · 기업소개 · 인재상 · 요구스택 top10 · 공고수 · 경력분포
    동명 기업이 여럿이면 status="ambiguous" + 후보 목록을 반환하고
    LLM 이 사용자에게 되묻게 한다

find_similar_companies(company_id, top=5)
    0.5×스택코사인 + 0.35×설명코사인 + 0.15×규모근접 (market.similarity)
    공통 스킬을 함께 반환해 근거를 설명할 수 있게 한다

compare_companies(company_ids[2~5])
    기업별 상위스택 · 공통스택 · 공고수 · 규모. 2개 미만이면 오류 반환

search_companies(query, size_type?, field?, top=10)
    pgvector(profile_embedding) + 메타 필터를 한 쿼리로.
    매칭 근거 문장을 함께 반환

평판·분위기는 데이터가 없다. 인재상·기업소개로 우회하고 "공고 기준"임을 명시.
"""
