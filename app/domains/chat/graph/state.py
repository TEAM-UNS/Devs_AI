"""ChatState — 그래프 노드가 주고받는 상태.

    session_id · user_id
    messages          벤더 중립 메시지 (LLMPort 타입만)
    profile           내 스킬 등 개인화 입력 (없을 수 있음)
    tool_calls        이번 턴의 툴 호출 기록 (arguments · result · latency · error)
    charts            chart_payload 목록
    blocked           스코프 가드 결과
    usage             입출력 토큰 누적

messages 에 벤더 타입이 들어오지 않게 한다. 어댑터가 경계에서 번역한다.
"""
