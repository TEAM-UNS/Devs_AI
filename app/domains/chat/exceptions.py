from app.core.exception.exceptions import AppException


class SessionNotFound(AppException):
    code = "SESSION_NOT_FOUND"

    def __init__(self):
        super().__init__(
            "세션을 찾을 수 없습니다.",
            status_code=404
        )


class UserNotFound(AppException):
    code = "USER_NOT_FOUND"

    def __init__(self):
        super().__init__(
            "사용자를 찾을 수 없습니다.",
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


class LLMRefused(AppException):
    code = "LLM_REFUSED"

    def __init__(self):
        super().__init__(
            "이 질문에는 답변할 수 없습니다. 질문을 바꿔서 다시 시도해주세요."
        )