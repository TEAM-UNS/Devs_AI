from typing import Optional

from app.core.exception.exceptions import AppException


class ReportNotFound(AppException):
    code = "REPORT_NOT_FOUND"

    def __init__(self, week_start_date: str, major_id: Optional[int]):
        super().__init__(
            "해당 주차의 리포트 데이터가 없습니다.",
            status_code=404,
            detail={
                "week_start_date": week_start_date,
                "major_id": major_id
            }
        )


class ReportDataIncomplete(AppException):
    code = "REPORT_DATA_INCOMPLETE"

    def __init__(self, missing: list[str]):
        super().__init__(
            "리포트를 만들 집계 데이터가 비어 있습니다.",
            status_code=422,
            detail={"missing": missing}
        )


class ReportAlreadyGenerated(AppException):
    code = "REPORT_ALREADY_GENERATED"

    def __init__(self, report_id: int):
        super().__init__(
            "이미 생성된 리포트입니다.",
            status_code=409,
            detail={"report_id": report_id}
        )