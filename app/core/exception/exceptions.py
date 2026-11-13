from typing import Any, Optional


class AppException(Exception):
    code = "INVALID_REQUEST"
    
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        detail: Optional[Any] = None
    ):
        super().__init__(message)

        self.message = message
        self.status_code = status_code
        self.detail = detail


class UpstreamError(AppException):
    code = "LLM_UNAVAILABLE"

    def __init__(self, message: str):
        super().__init__(
            message,
            status_code=502
        )
