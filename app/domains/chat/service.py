"""유스케이스: 세션 확보 → 그래프 실행 → 영속화.

    stream_answer(user_id, req)
        1. 세션 확보 (없으면 생성)
        2. guard: 레이트리밋 · 일일 토큰 예산 확인
        3. 그래프 실행 (thread_id = session_id)
        4. stream.py 로 SSE 이벤트 변환해 yield
        5. 종료 시 user/assistant 메시지 + 툴콜 + chart_payload 저장
           첫 턴이면 질문으로 제목 자동 생성
        6. 연결 끊김 감지 시 그래프 실행 취소 (토큰 낭비 방지)

    list_sessions / get_session_detail / rename / delete
    suggestions(profile)   정적 목록 + 프로필 기반 동적 생성

세션 조회는 항상 user_id 로 소유자 확인. 없으면 NotFoundError.
"""
