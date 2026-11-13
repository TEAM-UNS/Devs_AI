from app.core.config import get_settings
from app.infra.llm.port import LLMPort


def build_llm() -> LLMPort:
    if get_settings().llm_provider == "gemini":
        from app.infra.llm.adapters.gemini import GeminiLLM

        return GeminiLLM()

    from app.infra.llm.adapters.claude import ClaudeLLM

    return ClaudeLLM()