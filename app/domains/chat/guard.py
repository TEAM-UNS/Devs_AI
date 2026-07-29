"""스코프 검사 · 레이트리밋.

스코프
    채용 · 기술스택 · 기업 분석 범위 밖 질문은 툴 호출 없이 거부 응답.
    판정이 애매하면 통과시키고 LLM 이 판단하게 둔다(과차단 방지).
    거부 시에도 SSE 형식은 동일하게 유지한다.

레이트리밋 (redis)
    분당 요청수     CHAT_RATE_LIMIT_PER_MIN
    일일 토큰 예산  CHAT_DAILY_TOKEN_BUDGET (usage 를 응답 후 가산)
    초과 시 RateLimitError → 429

토큰 예산
    히스토리가 CHAT_HISTORY_TOKEN_BUDGET 을 넘으면 오래된 턴부터 제거
"""
