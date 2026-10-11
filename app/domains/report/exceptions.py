from typing import Optional

from datetime import date

from app.core.exception.exceptions import AppException


class ReportNotFound(AppException):
    code = "REPORT_NOT_FOUND"

    def __init__(self, report_date: date):
        super().__init__(
            "리포트를 찾을 수 없습니다.",
            status_code=404,
            detail={"report_id": report_date}
        )


class ReportDataIncomplete(AppException):
    code = "REPORT_DATA_INCOMPLETE"

    def __init__(self, report_id: int, missing: list[str]):
        super().__init__(
            "리포트를 만들 집계 데이터가 비어 있습니다.",
            status_code=422,
            detail={
                "report_id": report_id,
                "missing": missing
            }
        )


class ReportAlreadyGenerated(AppException):
    code = "REPORT_ALREADY_GENERATED"

    def __init__(self, report_id: int):
        super().__init__(
            "이미 생성된 리포트입니다. 다시 만들려면 force 를 쓰세요.",
            status_code=409,
            detail={"report_id": report_id}
        )


class ReportGenerationFailed(AppException):
    code = "REPORT_GENERATION_FAILED"

    def __init__(self, report_id: int, reason: Optional[str] = None):
        super().__init__(
            "리포트 생성에 실패했습니다.",
            status_code=502,
            detail={"report_id": report_id, "reason": reason}
        )
