"""개인화 툴 — 갭 분석. → market.queries

get_skill_gap(my_skills[], field?, company_ids?, top=15)
    내 스킬과 시장 수요의 차이를 계산한다.
    - 대상은 requirement in (required, tag) 만. preferred 는 제외
      (우대사항까지 "부족"으로 잡으면 목록이 무의미해진다)
    - company_ids 를 주면 해당 기업들의 요구 스택 기준으로 좁힌다
    - 반환: 부족 스킬 + 수요 순위(공고수 · 점유율)
    - my_skills 는 resolve_skill 로 정규화한 뒤 넘기게 유도한다

"합격 확률" 은 지원 결과 데이터가 없어 계산하지 않는다.
매칭률로 대체하고 "확률" 이라는 표현을 쓰지 않는다.
"""
