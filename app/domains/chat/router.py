"""/chat/* 엔드포인트.

    POST   /chat/stream                  질의 → SSE 스트리밍 응답
    POST   /chat/sessions                세션 생성
    GET    /chat/sessions                내 세션 목록 (페이징)
    GET    /chat/sessions/{id}           세션 상세 + 메시지 + 차트 복원
    PATCH  /chat/sessions/{id}           제목 수정
    DELETE /chat/sessions/{id}           삭제 (soft)
    GET    /chat/suggestions             추천 질문 (정적 + 프로필 기반)

공통
    - 인증: get_current_user (dev 헤더 / jwt sub)
    - 타 유저 세션 접근은 403 이 아니라 404 (존재 여부를 노출하지 않는다)
    - 라우터는 조립만. 로직은 service.py
"""
