# Gemini 임베딩 어댑터 테스트

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.exception.exceptions import UpstreamError
from app.infra.embedding.adapters.api import GeminiEmbedder
from app.infra.embedding.port import EmbedderPort

_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
    monkeypatch.setenv("EMBED_RPM", "0")
    monkeypatch.setenv("EMBED_TPM", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ok(n: int, dim: int = 1024, tokens: int = 100) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "embeddings": [{"values": [0.01] * dim} for _ in range(n)],
            "usageMetadata": {"promptTokenCount": tokens},
        },
    )


@respx.mock
async def test_each_text_becomes_its_own_request() -> None:
    route = respx.post(_URL).mock(return_value=_ok(3))
    vectors = await GeminiEmbedder().embed_documents(["가", "나", "다"])

    body = route.calls[0].request.read()
    import json

    payload = json.loads(body)
    assert len(payload["requests"]) == 3
    assert [r["content"]["parts"][0]["text"] for r in payload["requests"]] == ["가", "나", "다"]
    assert all(len(r["content"]["parts"]) == 1 for r in payload["requests"])
    assert len(vectors) == 3


@respx.mock
async def test_document_and_query_use_different_task_types() -> None:
    route = respx.post(_URL).mock(side_effect=[_ok(1), _ok(1)])

    embedder = GeminiEmbedder()
    await embedder.embed_documents(["문서"])
    await embedder.embed_query("질의")

    import json

    types = [json.loads(c.request.read())["requests"][0]["taskType"] for c in route.calls]
    assert types == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


@respx.mock
async def test_output_dimensionality_is_pinned_to_embed_dim() -> None:
    route = respx.post(_URL).mock(return_value=_ok(1))
    await GeminiEmbedder().embed_documents(["가"])

    import json

    payload = json.loads(route.calls[0].request.read())
    assert payload["requests"][0]["outputDimensionality"] == 1024
    assert payload["requests"][0]["model"] == "models/gemini-embedding-2"


@respx.mock
async def test_api_key_goes_in_the_header() -> None:
    route = respx.post(_URL).mock(return_value=_ok(1))
    await GeminiEmbedder().embed_documents(["가"])
    assert route.calls[0].request.headers["x-goog-api-key"] == "test-key"


async def test_empty_input_does_not_call_the_api() -> None:
    with respx.mock:
        route = respx.post(_URL).mock(return_value=_ok(0))
        assert await GeminiEmbedder().embed_documents([]) == []
        assert route.call_count == 0


@respx.mock
async def test_batch_is_capped_at_the_api_limit() -> None:
    route = respx.post(_URL).mock(side_effect=lambda req: _ok(_count(req)))

    embedder = GeminiEmbedder(batch_size=250)
    assert embedder.batch_size == GeminiEmbedder.MAX_BATCH

    vectors = await embedder.embed_documents([f"텍스트{i}" for i in range(250)])
    assert len(vectors) == 250
    assert route.call_count == 3
    assert all(_count(c.request) <= GeminiEmbedder.MAX_BATCH for c in route.calls)


def _count(request: httpx.Request) -> int:
    import json

    return len(json.loads(request.read())["requests"])


@respx.mock
async def test_oversized_text_is_truncated_not_rejected() -> None:
    route = respx.post(_URL).mock(return_value=_ok(1))
    await GeminiEmbedder().embed_documents(["가" * (GeminiEmbedder.MAX_INPUT_CHARS + 5000)])

    import json

    sent = json.loads(route.calls[0].request.read())["requests"][0]["content"]["parts"][0]["text"]
    assert len(sent) == GeminiEmbedder.MAX_INPUT_CHARS


@respx.mock
async def test_count_mismatch_is_rejected() -> None:
    respx.post(_URL).mock(return_value=_ok(2))
    with pytest.raises(UpstreamError, match="개수 불일치"):
        await GeminiEmbedder().embed_documents(["가", "나", "다"])


@respx.mock
async def test_dimension_mismatch_is_rejected() -> None:
    respx.post(_URL).mock(return_value=_ok(1, dim=3072))
    with pytest.raises(UpstreamError, match="차원 불일치"):
        await GeminiEmbedder().embed_documents(["가"])


@respx.mock
async def test_malformed_body_is_rejected() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"embeddings": "nope"}))
    with pytest.raises(UpstreamError, match="embeddings"):
        await GeminiEmbedder().embed_documents(["가"])


@respx.mock
async def test_client_error_is_fatal_and_not_retried() -> None:
    route = respx.post(_URL).mock(
        return_value=httpx.Response(403, json={"error": {"message": "API key not valid"}})
    )
    with pytest.raises(UpstreamError, match="API key not valid"):
        await GeminiEmbedder().embed_documents(["가"])
    assert route.call_count == 1


@respx.mock
async def test_server_error_is_retried_then_succeeds() -> None:
    route = respx.post(_URL).mock(side_effect=[httpx.Response(503, text="unavailable"), _ok(1)])
    vectors = await GeminiEmbedder(max_retry=3).embed_documents(["가"])
    assert len(vectors) == 1
    assert route.call_count == 2


@respx.mock
async def test_retries_are_exhausted_into_upstream_error() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(UpstreamError, match="재시도 2회 소진"):
        await GeminiEmbedder(max_retry=2).embed_documents(["가"])


def test_satisfies_the_port() -> None:
    assert isinstance(GeminiEmbedder(), EmbedderPort)


def test_missing_key_fails_at_construction() -> None:
    with pytest.raises(UpstreamError, match="GOOGLE_API_KEY"):
        GeminiEmbedder(api_key="")


@respx.mock
async def test_actual_tokens_feed_the_budget() -> None:
    respx.post(_URL).mock(return_value=_ok(1, tokens=777))

    embedder = GeminiEmbedder()
    before = embedder._budget.ratio
    await embedder.embed_documents(["가" * 1000])

    assert embedder.total_tokens == 777
    assert embedder.call_count == 1
    assert embedder._budget.samples == 1
    assert embedder._budget.ratio != before
