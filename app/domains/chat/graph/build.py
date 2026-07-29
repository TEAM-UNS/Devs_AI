"""StateGraph 조립 · checkpointer.

    load_context ─ guard ─┬─ (차단) ──────────── END
                          └─ agent ⇄ tools
                                └─ persist ──── END

설정
    checkpointer      AsyncPostgresSaver. thread_id = session_id
                      앱 lifespan 에서 .setup() 1회 (chat 스키마에 테이블 생성)
    recursion_limit   CHAT_RECURSION_LIMIT(8) — 툴 3~4회까지 허용,
                      초과 시 현재까지 내용으로 마감
    그래프 인스턴스는 프로세스당 1개 (lru_cache)
"""
