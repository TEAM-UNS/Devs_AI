from collections.abc import Iterator
from contextlib import contextmanager

from langchain_core.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelRateLimitError,
    ModelTimeoutError,
)

from app.core.exception.exceptions import UpstreamError


@contextmanager
def upstream_guard() -> Iterator[None]:
    try:
        yield
    except (
        ModelRateLimitError,
        ModelAPIError,
        ModelConnectionError,
        ModelTimeoutError,
    ) as exc:
        raise UpstreamError(str(exc)) from exc