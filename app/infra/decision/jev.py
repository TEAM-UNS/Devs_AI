import logging
from functools import cache
from typing import Any, Optional

from langfuse.types import TraceContext
from typesafe_sdk import AsyncTypeSafeClient, ChoiceAnswer, Questions, RetryPolicy

from app.core.config import get_settings
from app.infra.llm.tracing import start_generation


logger = logging.getLogger(__name__)


@cache
def _client() -> AsyncTypeSafeClient:
    settings = get_settings()
    return AsyncTypeSafeClient(
        api_key=settings.typesafe_api_key,
        retry=RetryPolicy(max_retries=0),
        timeout=settings.jev_timeout_seconds,
    )


async def ask(
    state: dict[str, Any],
    questions: Questions,
    trace_context: Optional[TraceContext] = None,
) -> Optional[dict[str, Any]]:
    settings = get_settings()
    with start_generation("jev", state, trace_context) as generation:
        try:
            response = await _client().system_one(state=state, questions=questions)
        except Exception as e:
            logger.warning("jev 호출 실패 — %s", e)
            if generation is not None:
                generation.update(level="ERROR", status_message=str(e))
            return None

        answers = dict(response.answers)
        if generation is not None:
            generation.update(
                model=response.model,
                output={
                    name: a.choice
                    if isinstance(a, ChoiceAnswer) else a.noul
                    for name, a in answers.items()
                },
                usage_details={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
                cost_details={
                    "input": response.usage.input_tokens * settings.jev_input_usd_per_mtok / 1_000_000,
                    "output": 0.0,
                },
                metadata={
                    "confidence": {
                        name: a.confidence
                        for name, a in answers.items()
                        if isinstance(a, ChoiceAnswer)
                    }
                },
            )
    return answers


async def close_jev() -> None:
    if _client.cache_info().currsize:
        await _client().aclose()