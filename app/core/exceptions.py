"""도메인 예외 → HTTP 매핑.

계층
    AppError
      ├─ UnauthorizedError      401
      ├─ ForbiddenError         403
      ├─ NotFoundError          404   (타 유저 세션 접근도 여기로 — 존재 노출 금지)
      ├─ RateLimitError         429   (분당 요청 · 일일 토큰 초과)
      ├─ UpstreamError          502   (LLM · 임베딩 API 실패)
      └─ ValidationError        422

register_exception_handlers(app) 이 FastAPI 핸들러를 붙인다.
도메인 코드는 HTTP 상태를 모른다. 예외만 던진다.
"""
