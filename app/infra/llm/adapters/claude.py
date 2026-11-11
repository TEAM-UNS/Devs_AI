from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from app.core.config import get_settings
from app.infra.llm.port import StopReason


class ClaudeLLM:
    def __init__(self):
        settings = get_settings()

        self._model = ChatAnthropic(
            model=settings.claude_model,
            api_key=settings.anthropic_api_key,
            max_tokens=settings.llm_max_tokens,
            effort="low",
            max_retries=settings.llm_max_retry,
        )


    def chat(self) -> BaseChatModel:
        return self._model


    def structured[M: BaseModel](self, schema: type[M]) -> Runnable[LanguageModelInput, M]:
        return self._model.with_structured_output(schema, method="json_schema")


    @staticmethod
    def stop_reason(message: AIMessage) -> StopReason:
        match message.response_metadata.get("stop_reason"):
            case "refusal":
                return StopReason.REFUSED
            case "max_tokens" | "model_context_window_exceeded":
                return StopReason.TRUNCATED
            case _:
                return StopReason.DONE