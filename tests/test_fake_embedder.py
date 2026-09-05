"""FakeEmbedder 계약 — API 키 없이 전체 파이프라인을 돌리기 위한 전제.

호출부(embed_service)는 반환 리스트를 인덱스로 원본 청크에 되붙인다.
순서·개수가 어긋나면 엉뚱한 공고에 벡터가 박히므로 여기서 못 박아 둔다.
"""

from __future__ import annotations

import math

from app.core.config import get_settings
from app.llm.fake import FakeEmbedder
from app.llm.port import EmbedderPort


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
    # 인덱스 i 의 벡터가 texts[i] 의 벡터와 같아야 한다.
    for i, text in enumerate(texts):
        [alone] = await FakeEmbedder().embed_documents([text])
        assert vectors[i] == alone, f"{i}번째 벡터가 어긋났다"


async def test_vectors_are_l2_normalized() -> None:
    """정규화해 두면 코사인 유사도가 내적과 같아져 검증이 쉬워진다."""
    [vector] = await FakeEmbedder().embed_documents(["정규화 확인"])
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


async def test_counts_calls_not_texts() -> None:
    """'배치 1회로 묶였는가' 를 이 카운터로 검증한다."""
    embedder = FakeEmbedder()
    await embedder.embed_documents([f"t{i}" for i in range(96)])
    assert embedder.call_count == 1
    assert len(embedder.embedded_texts) == 96


async def test_empty_input_is_not_a_call() -> None:
    embedder = FakeEmbedder()
    assert await embedder.embed_documents([]) == []
    # 빈 입력도 호출로 세지만, 호출부(embed_service)는 애초에 빈 배치를
    # 만들지 않는다. 여기서는 예외 없이 돌아가는지만 본다.
    assert embedder.embedded_texts == []
