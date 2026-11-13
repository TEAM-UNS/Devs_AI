from functools import cache
from typing import Any, Optional

from contextlib import AbstractContextManager, nullcontext

from langchain_core.callbacks import BaseCallbackManager
from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from langfuse.types import TraceContext

from app.core.config import get_settings


@cache
def _client() -> Optional[Langfuse]:
    settings = get_settings()
    if not settings.langfuse_public_key:
        return None

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        environment=settings.langfuse_environment,
    )


def trace_config(
    name: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if _client() is None:
        return {"run_name": name}

    trace_metadata: dict[str, Any] = dict(metadata or {})
    if session_id is not None:
        trace_metadata["langfuse_session_id"] = session_id
    if user_id is not None:
        trace_metadata["langfuse_user_id"] = user_id
    if tags:
        trace_metadata["langfuse_tags"] = tags

    return {
        "run_name": name,
        "callbacks": [CallbackHandler(public_key=get_settings().langfuse_public_key)],
        "metadata": trace_metadata,
    }


def node_trace_context(config: RunnableConfig) -> Optional[TraceContext]:
    manager = config.get("callbacks")
    if not isinstance(manager, BaseCallbackManager):
        return None

    for handler in manager.handlers:
        if isinstance(handler, CallbackHandler):
            observation = handler._runs.get(manager.parent_run_id)
            if observation is not None:
                return {
                    "trace_id": observation.trace_id,
                    "parent_span_id": observation.id
                }
    return None


def start_generation(
    name: str,
    input: Any,
    trace_context: Optional[TraceContext] = None,
) -> AbstractContextManager[Any]:
    client = _client()
    if client is None:
        return nullcontext()
    return client.start_as_current_observation(
        as_type="generation",
        name=name,
        input=input,
        trace_context=trace_context,
    )


def shutdown_tracing():
    client = _client()
    if client is not None:
        client.shutdown()
