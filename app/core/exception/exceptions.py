# 앱 공통 예외 기반 클래스

class AppException(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)

        self.message = message
        self.status_code = status_code


class UpstreamError(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502)
