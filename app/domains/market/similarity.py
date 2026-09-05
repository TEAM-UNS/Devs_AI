"""유사 기업 계산.

점수 = 0.5 × 스택 코사인 + 0.35 × 설명 코사인 + 0.15 × 규모 근접

    스택 코사인   두 기업의 요구 스킬 벡터(스킬별 공고 비중) 코사인
    설명 코사인   company.profile_embedding 코사인 (pgvector <=>)
    규모 근접     employee_count 로그 스케일 거리 → 0~1
                  employee_count 가 없으면 size_type 구간 거리로 대체

반환에 공통 스킬 목록을 함께 실어 LLM 이 근거를 설명할 수 있게 한다.
"""
