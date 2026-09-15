# 채팅 모델 선택

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import get_settings


settings = get_settings()

def build_chat_model() -> BaseChatModel:
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        thinking_level="low",
        max_retries=settings.llm_max_retry,
    )