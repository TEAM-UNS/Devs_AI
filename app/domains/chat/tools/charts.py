"""툴 결과 → 차트 페이로드 변환.

    tool_name → chart type
        get_popular_skills      bar
        get_rising_skills       bar (증감률)
        get_stacks_by_segment   grouped bar
        get_salary_stats        box (분위수 있음) | bar (없으면)
        get_skill_demand        bar
        get_skill_gap           bar
        그 외                   None → graph 이벤트 미전송

페이로드 규칙
    - 툴이 반환한 원본 수치를 그대로 싣는다. LLM 이 요약한 값을 쓰지 않는다
    - type · title · unit · series[] · meta(sample_size · disclosure_rate) 구조
    - chat_tool_call.chart_payload 에 저장 → 세션 재진입 시 LLM 재호출 없이 복원
    - 렌더링은 프론트 책임. 여기서는 색상·크기 같은 표현을 결정하지 않는다
"""
