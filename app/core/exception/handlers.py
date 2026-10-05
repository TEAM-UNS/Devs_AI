import logging

from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.status import (
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.core.exception.exceptions import AppException


logger = logging.getLogger(__name__)


def _error(
    status_code: int,
    code: str,
    message: str,
    detail: Optional[Any] = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "detail": detail
            }
        },
    )


def register_exception_handlers(app: FastAPI):
    @app.exception_handler(AppException)
    async def handle_app_exception(
            request: Request,
            exc: AppException
    ) -> JSONResponse:
        return _error(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            detail=exc.detail,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError
    ) -> JSONResponse:
        return _error(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_ERROR",
            message="요청 값이 올바르지 않습니다.",
            detail=exc.errors(),
        )

    @app.exception_handler(Exception)
    async def handle_uncaught_exception(
        request: Request,
        exc: Exception
    ):
        logger.exception(
            "Uncaught exception | path=%s",
            request.url.path,
        )

        return _error(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="서버 오류가 발생했습니다.",
        )