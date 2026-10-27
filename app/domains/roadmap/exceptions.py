from app.core.exception.exceptions import AppException


class RoadmapNotFound(AppException):
    code = "ROADMAP_NOT_FOUND"

    def __init__(self):
        super().__init__(
            "로드맵을 찾을 수 없습니다.",
            status_code=404
        )