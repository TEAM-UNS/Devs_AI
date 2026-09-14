# 가짜 임베더 테스트

from __future__ import annotations

import math

from app.core.config import get_settings
from app.llm.embed.fake import FakeEmbedder
from app.llm.embed.port import EmbedderPort


def test_satisfies_the_port() -> None:
    assert isinstance(FakeEmbedder(), EmbedderPort)


async def test_dimension_matches_schema() -> None:
    vectors = await FakeEmbedder().embed_documents(["백엔드 개발자"])
    assert len(vectors) == 1
    assert len(vectors[0]) == get_settings().embed_dim


async def test_is_deterministic() -> None:
    a = await FakeEmbedder().embed_documents(["Python"])
    b = await FakeEmbedder().embed_documents(["Python"])
    assert a == b


async def test_different_text_gives_different_vector() -> None:
    embedder = FakeEmbedder()
    [python, kotlin] = await embedder.embed_documents(["Python", "Kotlin"])
    assert python != kotlin


async def test_preserves_order_and_count() -> None:
    embedder = FakeEmbedder()
    texts = [f"청크 {i}" for i in range(50)]
    vectors = await embedder.embed_documents(texts)

    assert len(vectors) == len(texts)
    for i, text in enumerate(texts):
        [alone] = await FakeEmbedder().embed_documents([text])
        assert vectors[i] == alone, f"{i}번째 벡터가 어긋났다"


async def test_vectors_are_l2_normalized() -> None:
    [vector] = await FakeEmbedder().embed_documents(["정규화 확인"])
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


async def test_counts_calls_not_texts() -> None:
    embedder = FakeEmbedder()
    await embedder.embed_documents([f"t{i}" for i in range(96)])
    assert embedder.call_count == 1
    assert len(embedder.embedded_texts) == 96


async def test_empty_input_is_not_a_call() -> None:
    embedder = FakeEmbedder()
    assert await embedder.embed_documents([]) == []
    assert embedder.embedded_texts == []
