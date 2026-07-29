"""그래프 노드.

    load_context   세션 히스토리 · 프로필 로드, 토큰 예산에 맞게 절삭
    guard          스코프 검사 + 레이트리밋. 차단이면 즉시 종료 경로로
    agent          LLM 호출. 툴 호출이 오면 실행 후 결과를 되먹임
                   툴 실행 결과는 tool_calls / charts 에 누적
    persist        chat_message + chat_tool_call(chart_payload) 저장,
                   세션 message_count · last_message_at 갱신

규칙
    - 툴이 전부 실패하면 "데이터를 조회하지 못했다"고 명시하게 한다
    - 툴 결과에 없는 수치를 LLM 이 지어내지 않도록 시스템 프롬프트에서 강제
    - 연봉 수치는 sample_size · disclosure_rate 를 반드시 함께 서술
"""
