"""LLM · 임베딩 어댑터 예외."""

from app.core.exception.errors import AppException


class UpstreamError(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502)
