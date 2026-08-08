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

★ 지금은 클래스 계층만 있다. register_exception_handlers 는 라우터가 붙는
  시점(챗봇 작업)에 여기에 추가한다. 임베딩 어댑터가 UpstreamError 를
  던져야 해서 계층을 먼저 세웠다.
"""

from __future__ import annotations


class AppError(Exception):
    """도메인 예외의 뿌리. status_code 는 HTTP 핸들러만 참조한다."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.__doc__ or self.code)
        self.message = message
        if code:
            self.code = code


class UnauthorizedError(AppError):
    """인증 실패."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    """권한 없음."""

    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    """대상 없음. 타 유저 리소스 접근도 존재를 알리지 않기 위해 여기로 보낸다."""

    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    """입력값 오류."""

    status_code = 422
    code = "invalid_request"


class RateLimitError(AppError):
    """분당 요청수 · 일일 토큰 예산 초과."""

    status_code = 429
    code = "rate_limited"


class UpstreamError(AppError):
    """LLM · 임베딩 등 외부 API 실패 (재시도 소진 포함)."""

    status_code = 502
    code = "upstream_error"
