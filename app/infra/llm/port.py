from enum import StrEnum
from typing import Protocol, runtime_checkable

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel


class StopReason(StrEnum):
    DONE = "done"
    TRUNCATED = "truncated"
    REFUSED = "refused"


@runtime_checkable
class LLMPort(Protocol):
    def chat(self) -> BaseChatModel:
        ...

    def system(self, text: str) -> SystemMessage:
        ...
    
    def structured[M: BaseModel](self, schema: type[M]) -> Runnable[LanguageModelInput, M]:
        ...

    def stop_reason(self, message: AIMessage) -> StopReason:
        ...