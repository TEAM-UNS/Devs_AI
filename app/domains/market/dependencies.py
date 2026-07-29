"""market 의존성 — 세션이 주입된 queries / repository 핸들.

    QueriesDep      챗봇 툴이 받는 읽기 핸들
    RepositoryDep   크롤러 태스크가 받는 쓰기 핸들 (워커 세션)

운영에서는 두 핸들이 서로 다른 DB 롤(ai_chat / ai_crawler)로 접속한다.
"""
