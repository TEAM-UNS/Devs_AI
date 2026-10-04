from app.core.exception.exceptions import AppException


class SessionNotFound(AppException):
    def __init__(self):
        super().__init__(
            "세션을 찾을 수 없습니다.",
            status_code=404
        )