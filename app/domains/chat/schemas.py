"""chat DTO — 요청/응답 · SSE 이벤트 페이로드.

요청/응답
    ChatRequest       session_id? · message · profile?(내 스킬 등)
    SessionOut · SessionListOut · MessageOut · ToolCallOut
    SuggestionOut

SSE 이벤트 (stream.py 가 직렬화)
    event: start        session_id · message_id
    event: tool_start   tool_name · arguments
    event: token        text delta
    event: graph        chart_payload (툴 원본 수치. 프론트가 렌더링)
    event: done         usage · finish_reason
    event: error        code · message

graph 이벤트는 token 스트림과 독립적으로 나간다.
차트화 불가한 툴 결과는 graph 를 보내지 않는다.
"""
