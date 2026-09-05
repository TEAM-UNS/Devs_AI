"""GeminiEmbedder — REST 계약과 방어선.

respx 로 HTTP 를 가로챈다. 실제 호출은 하지 않는다(키·요금·레이트리밋).
여기서 못 박는 것은 셋이다.

1. 요청 형태 — 텍스트 1개당 request 1개.
   ★ 이게 이 파일의 존재 이유다. 공식 SDK 는 문자열 리스트를 하나의 content
     parts 로 합쳐 **벡터 1개**를 돌려준다. 예외도 경고도 없다. embed_service
     는 반환 리스트를 인덱스로 원본 청크에 되붙이므로, 그게 통과하면 엉뚱한
     공고에 벡터가 박히고 나중에 "검색이 이상하다" 로만 드러난다.

2. 개수·차원 검증 — DB 까지 흘려보내지 않는다.

3. 재시도/치명 구분 — 400·401·403 은 즉시 포기, 5xx·429 는 재시도.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.llm.exceptions import UpstreamError
from app.llm.gemini_embed_adapter import MAX_BATCH, MAX_INPUT_CHARS, GeminiEmbedder
from app.llm.port import EmbedderPort

_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    """.env 값이 새어 들어오지 않게 고정한다."""
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


# ── 요청 형태 ───────────────────────────────────────────────────────────────
@respx.mock
async def test_each_text_becomes_its_own_request() -> None:
    """★ 회귀. 3개를 보내면 request 3개여야 한다.

    한 content 의 parts 로 몰면 서버가 이어 붙여 벡터 1개를 준다.
    """
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
    """포트가 메서드를 나눠 둔 이유. 같은 taskType 을 쓰면 검색 품질이 떨어진다."""
    route = respx.post(_URL).mock(side_effect=[_ok(1), _ok(1)])

    embedder = GeminiEmbedder()
    await embedder.embed_documents(["문서"])
    await embedder.embed_query("질의")

    import json

    types = [json.loads(c.request.read())["requests"][0]["taskType"] for c in route.calls]
    assert types == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


@respx.mock
async def test_output_dimensionality_is_pinned_to_embed_dim() -> None:
    """기본 출력은 1024 가 아니다. 명시하지 않으면 vector(1024) 에 안 들어간다."""
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
    """빈 배치로 요청을 쏘면 400 이다. 호출 전에 끊는다."""
    with respx.mock:
        route = respx.post(_URL).mock(return_value=_ok(0))
        assert await GeminiEmbedder().embed_documents([]) == []
        assert route.call_count == 0


# ── 배치 상한 ───────────────────────────────────────────────────────────────
@respx.mock
async def test_batch_is_capped_at_the_api_limit() -> None:
    """★ 서버 상한 100. 101개는 400 이라 클라이언트에서 먼저 쪼갠다."""
    route = respx.post(_URL).mock(side_effect=lambda req: _ok(_count(req)))

    embedder = GeminiEmbedder(batch_size=250)
    assert embedder.batch_size == MAX_BATCH

    vectors = await embedder.embed_documents([f"텍스트{i}" for i in range(250)])
    assert len(vectors) == 250
    assert route.call_count == 3  # 100 + 100 + 50
    assert all(_count(c.request) <= MAX_BATCH for c in route.calls)


def _count(request: httpx.Request) -> int:
    import json

    return len(json.loads(request.read())["requests"])


# ── 길이 상한 ───────────────────────────────────────────────────────────────
@respx.mock
async def test_oversized_text_is_truncated_not_rejected() -> None:
    """★ 초과하면 400 이고, 400 이면 그 배치의 공고 전부가 embed_hash 를 못 닫아
    다음 백필에서 같은 자리에서 또 죽는다 — 영구 루프다. 꼬리를 자른다."""
    route = respx.post(_URL).mock(return_value=_ok(1))
    await GeminiEmbedder().embed_documents(["가" * (MAX_INPUT_CHARS + 5000)])

    import json

    sent = json.loads(route.calls[0].request.read())["requests"][0]["content"]["parts"][0]["text"]
    assert len(sent) == MAX_INPUT_CHARS


# ── 검증 ────────────────────────────────────────────────────────────────────
@respx.mock
async def test_count_mismatch_is_rejected() -> None:
    """★ 인덱스로 되붙이는 구조라 개수가 어긋나면 전부 밀린다."""
    respx.post(_URL).mock(return_value=_ok(2))
    with pytest.raises(UpstreamError, match="개수 불일치"):
        await GeminiEmbedder().embed_documents(["가", "나", "다"])


@respx.mock
async def test_dimension_mismatch_is_rejected() -> None:
    """vector(1024) 컬럼과 어긋나면 INSERT 단계에서야 터진다. 여기서 막는다."""
    respx.post(_URL).mock(return_value=_ok(1, dim=3072))
    with pytest.raises(UpstreamError, match="차원 불일치"):
        await GeminiEmbedder().embed_documents(["가"])


@respx.mock
async def test_malformed_body_is_rejected() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"embeddings": "nope"}))
    with pytest.raises(UpstreamError, match="embeddings"):
        await GeminiEmbedder().embed_documents(["가"])


# ── 재시도 ──────────────────────────────────────────────────────────────────
@respx.mock
async def test_client_error_is_fatal_and_not_retried() -> None:
    """키가 틀렸는데 3번 더 물어볼 이유가 없다."""
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


# ── 계약 · 계량 ─────────────────────────────────────────────────────────────
def test_satisfies_the_port() -> None:
    assert isinstance(GeminiEmbedder(), EmbedderPort)


def test_missing_key_fails_at_construction() -> None:
    """임베딩을 한참 돌린 뒤가 아니라 만들 때 터져야 한다."""
    with pytest.raises(UpstreamError, match="GOOGLE_API_KEY"):
        GeminiEmbedder(api_key="")


@respx.mock
async def test_actual_tokens_feed_the_budget() -> None:
    """★ SDK 를 안 쓰는 이유 중 하나. usageMetadata 가 없으면 추정이 자기
    오차를 영원히 모른다."""
    respx.post(_URL).mock(return_value=_ok(1, tokens=777))

    embedder = GeminiEmbedder()
    before = embedder._budget.ratio
    await embedder.embed_documents(["가" * 1000])

    assert embedder.total_tokens == 777
    assert embedder.call_count == 1
    assert embedder._budget.samples == 1
    assert embedder._budget.ratio != before
