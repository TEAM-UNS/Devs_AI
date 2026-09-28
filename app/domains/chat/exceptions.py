from app.core.exception.exceptions import AppException


class SessionNotFound(AppException):
    def __init__(self, session_id: int):
        super().__init__(
            f"세션 {session_id} 을 찾을 수 없습니다.",
            status_code=404
        )