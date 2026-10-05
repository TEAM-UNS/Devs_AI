from app.core.exception.exceptions import AppException


class SessionNotFound(AppException):
    code = "SESSION_NOT_FOUND"

    def __init__(self):
        super().__init__(
            "세션을 찾을 수 없습니다.",
            status_code=404
        )


class RateLimited(AppException):
    code = "RATE_LIMITED"

    def __init__(self, message: str, reset: int):
        super().__init__(
            message,
            status_code=429,
            detail={"reset": reset}
        )