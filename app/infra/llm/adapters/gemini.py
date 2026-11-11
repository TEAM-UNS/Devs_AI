from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from app.core.config import get_settings
from app.infra.llm.port import StopReason


class GeminiLLM:
    def __init__(self):
        settings = get_settings()

        self._model = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            thinking_level="low",
            max_retries=settings.llm_max_retry,
        )


    def chat(self) -> BaseChatModel:
        return self._model


    def structured[M: BaseModel](self, schema: type[M]) -> Runnable[LanguageModelInput, M]:
        return self._model.with_structured_output(schema)


    @staticmethod
    def stop_reason(message: AIMessage) -> StopReason:
        match message.response_metadata.get("finish_reason"):
            case "SAFETY" | "PROHIBITED_CONTENT" | "BLOCKLIST" | "SPII" | "RECITATION":
                return StopReason.REFUSED
            case "MAX_TOKENS":
                return StopReason.TRUNCATED
            case _:
                return StopReason.DONE