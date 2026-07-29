"""기술 관계 툴 — 연관 기술 · 수요 · 스킬명 해소. → market.queries

get_related_skills(skill, field?, requirement?, top=10)
    동반 스킬 · 동시출현수 · NPMI (동시출현 집계 기반)

get_skill_demand(skill)
    분야별 · 규모별 · 경력별 수요 분포 (posting_skill 분해)

resolve_skill(query)
    후보 스킬 3~5개 + 유사도. skill.embedding 을 메모리에서 코사인 비교.
    오탈자·한글표기("스프링부트")를 정규화 스킬명으로 잇는 용도.
    다른 툴이 스킬명을 못 찾았을 때 먼저 호출하도록 description 에 명시.
"""
