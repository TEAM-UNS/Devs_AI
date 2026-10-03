# Gemini 생성 어댑터 테스트

from types import SimpleNamespace
from typing import Any, Optional

import pytest
from google.genai import errors, types

from app.llm.exceptions import UpstreamError
from app.llm.generation import generation
from app.llm.generation.generation import GeminiGenerator


class FakeModels:
    def __init__(
        self,
        response: Optional[types.GenerateContentResponse] = None,
        error: Optional[Exception] = None,
    ):
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Optional[types.GenerateContentResponse]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(generation.settings, "google_api_key", "test-key")
    monkeypatch.setattr(generation.settings, "gemini_model", "test-model")


def _generator(models: FakeModels) -> GeminiGenerator:
    generator = GeminiGenerator()
    generator._client = SimpleNamespace(aio=SimpleNamespace(models=models))  # type: ignore[assignment]
    return generator


def _response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


async def test_complete_returns_text_with_system_and_thinking() -> None:
    models = FakeModels(_response("이번 주 백엔드 공고는 1,204건입니다"))

    assert await _generator(models).complete("집계 수치", "리포트 작성자") == "이번 주 백엔드 공고는 1,204건입니다"

    call = models.calls[0]
    assert call["model"] == "test-model"
    assert call["contents"] == "집계 수치"
    assert call["config"].system_instruction == "리포트 작성자"
    assert call["config"].thinking_config.thinking_level == types.ThinkingLevel.LOW


async def test_empty_text_is_an_error() -> None:
    models = FakeModels(types.GenerateContentResponse(candidates=[]))

    with pytest.raises(UpstreamError, match="비어"):
        await _generator(models).complete("집계 수치", "리포트 작성자")


async def test_api_error_becomes_upstream_error() -> None:
    error = errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
    models = FakeModels(error=error)

    with pytest.raises(UpstreamError, match="404"):
        await _generator(models).complete("집계 수치", "리포트 작성자")
