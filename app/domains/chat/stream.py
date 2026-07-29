"""astream_events → SSE 직렬화.

매핑
    on_chat_model_stream  → event: token
    on_tool_start         → event: tool_start
    on_tool_end           → charts.py 로 payload 생성 → event: graph
    graph 종료            → event: done (usage · finish_reason)
    예외                  → event: error

규칙
    - 토큰은 도착 즉시 흘린다. 버퍼링 금지
    - graph 이벤트는 툴 원본 수치를 그대로 싣는다(LLM 이 요약한 값 아님)
    - 클라이언트 disconnect 감지 시 즉시 중단하고 그래프를 취소
    - 하트비트(comment ping)로 프록시 타임아웃 방지
"""
