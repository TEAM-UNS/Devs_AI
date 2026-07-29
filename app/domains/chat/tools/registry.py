"""ALL_TOOLS — 툴 스펙(JSON Schema) + 실행 핸들러 매핑.

13개
    trend.py     get_popular_skills · get_rising_skills
                 get_stacks_by_segment · get_salary_stats
    skill.py     get_related_skills · get_skill_demand · resolve_skill
    company.py   get_company_profile · find_similar_companies
                 compare_companies · search_companies
    search.py    search_postings
    personal.py  get_skill_gap
    meta.py      get_data_coverage

스펙은 벤더 중립 dict 이며 llm/chat_adapter.py 가 벤더 포맷으로 변환한다.
툴 description 은 LLM 이 읽는 유일한 사용설명서다. 특히
    - search_postings: "순위·비율 질문에는 사용 금지" 를 명시
    - get_data_coverage: "시계열·신뢰도 질문 전 선행 호출" 을 명시
"""
