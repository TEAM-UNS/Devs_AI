from app.core.exception.exceptions import AppException


class RoadmapNotFound(AppException):
    code = "ROADMAP_NOT_FOUND"

    def __init__(self):
        super().__init__(
            "로드맵을 찾을 수 없습니다.",
            status_code=404
        )


class RoadmapGenerationFailed(AppException):
    code = "ROADMAP_GENERATION_FAILED"

    def __init__(self, reason: str):
        super().__init__(
            "로드맵 생성에 실패했습니다.",
            status_code=502,
            detail={"reason": reason}
        )