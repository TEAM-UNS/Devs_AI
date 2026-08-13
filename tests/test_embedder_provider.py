"""build_embedder 의 제공자 분기 — 로컬/API 를 갈아끼우는 지점.

여기가 틀리면 조용히 망가진다. 의도와 다른 어댑터가 붙어도 벡터는 정상적으로
1024 차원으로 들어가고 INSERT 도 통과하기 때문이다. 나중에 검색이 이상하다는
형태로만 드러난다. 그래서 "어떤 설정에서 어떤 어댑터가 나오는가" 를 못 박는다.

LocalEmbedder 는 __init__ 에서 모델을 올리지 않는다(첫 임베딩 때 지연 로드).
그래서 이 파일의 테스트는 torch 없이도 돌고 빠르다. 실제 추론까지 확인하는
테스트는 맨 아래에 환경변수로 분리해 뒀다.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_settings
from app.llm.embed_adapter import VoyageEmbedder, build_embedder
from app.llm.fake import FakeEmbedder
from app.llm.local_embed_adapter import LocalEmbedder
from app.llm.port import EmbedderPort


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings 는 lru_cache 다. 환경변수를 바꿨으면 비워야 반영된다."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    """.env 값이 새어 들어오지 않게 임베딩 관련 변수를 고정한다."""

    def _set(**kwargs: str) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", kwargs.pop("VOYAGE_API_KEY", "test-key"))
        for key, value in kwargs.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    return _set


# ── 명시적 지정 ─────────────────────────────────────────────────────────────
def test_local_selects_local_embedder(env) -> None:
    env(EMBED_PROVIDER="local")
    assert isinstance(build_embedder(), LocalEmbedder)


def test_voyage_selects_voyage_embedder(env) -> None:
    env(EMBED_PROVIDER="voyage")
    assert isinstance(build_embedder(), VoyageEmbedder)


def test_fake_selects_fake_embedder(env) -> None:
    env(EMBED_PROVIDER="fake")
    assert isinstance(build_embedder(), FakeEmbedder)


# ── auto — EMBED_PROVIDER 도입 전의 동작을 그대로 유지해야 한다 ─────────────
def test_auto_uses_voyage_when_key_present(env) -> None:
    env(EMBED_PROVIDER="auto", VOYAGE_API_KEY="test-key")
    assert isinstance(build_embedder(), VoyageEmbedder)


def test_auto_falls_back_to_fake_without_key(env) -> None:
    env(EMBED_PROVIDER="auto", VOYAGE_API_KEY="")
    assert isinstance(build_embedder(), FakeEmbedder)


def test_local_does_not_need_voyage_key(env) -> None:
    """로컬만 쓰는 환경에서 키를 요구하면 안 된다."""
    env(EMBED_PROVIDER="local", VOYAGE_API_KEY="")
    assert isinstance(build_embedder(), LocalEmbedder)


# ── force_fake 는 제공자보다 우선한다 ───────────────────────────────────────
@pytest.mark.parametrize("provider", ["auto", "local", "voyage", "fake"])
def test_force_fake_overrides_provider(env, provider: str) -> None:
    env(EMBED_PROVIDER=provider)
    assert isinstance(build_embedder(force_fake=True), FakeEmbedder)


# ── 잘못된 값은 기동 시점에 막는다 ──────────────────────────────────────────
def test_unknown_provider_is_rejected(env) -> None:
    """Literal 이라 pydantic 이 거른다. 오타로 조용히 fake 가 되면 안 된다."""
    with pytest.raises(Exception, match="embed_provider|EMBED_PROVIDER"):
        env(EMBED_PROVIDER="bge")
        get_settings()


# ── LocalEmbedder 계약 ──────────────────────────────────────────────────────
def test_local_satisfies_the_port(env) -> None:
    env(EMBED_PROVIDER="local")
    assert isinstance(LocalEmbedder(), EmbedderPort)


def test_local_reads_its_own_batch_size(env) -> None:
    """API 쪽 EMBED_BATCH_SIZE(96)를 따라가면 안 된다.

    96 은 네트워크 왕복을 줄이려는 값이고 로컬 GPU 에서는 31% 느리다.
    """
    env(EMBED_PROVIDER="local", EMBED_BATCH_SIZE="96", EMBED_LOCAL_BATCH_SIZE="32")
    assert LocalEmbedder().batch_size == 32


def test_local_construction_does_not_load_the_model(env) -> None:
    """모델 로드는 약 6초다. 워커 기동만 하고 임베딩을 안 할 수도 있으므로
    첫 임베딩 때까지 미룬다."""
    env(EMBED_PROVIDER="local")
    assert LocalEmbedder()._model is None


async def test_local_empty_input_short_circuits(env) -> None:
    """빈 입력에 모델을 올리면 안 된다 — 로드 비용만 물고 결과는 빈 리스트다."""
    env(EMBED_PROVIDER="local")
    embedder = LocalEmbedder()
    assert await embedder.embed_documents([]) == []
    assert embedder._model is None


# ── 실제 추론 (모델 다운로드가 필요해 기본에서는 건너뛴다) ──────────────────
#     RUN_LOCAL_EMBED_TEST=1 uv run pytest tests/test_embedder_provider.py
@pytest.mark.skipif(
    os.getenv("RUN_LOCAL_EMBED_TEST") != "1",
    reason="모델 다운로드·로드가 필요하다. RUN_LOCAL_EMBED_TEST=1 로 켠다.",
)
async def test_local_real_inference(env) -> None:
    pytest.importorskip("sentence_transformers")
    from app.core.enums import EMBEDDING_DIM

    env(EMBED_PROVIDER="local")
    embedder = LocalEmbedder()

    texts = ["백엔드 개발자 채용", "프론트엔드 개발자 채용"]
    vectors = await embedder.embed_documents(texts)

    assert len(vectors) == len(texts)
    assert all(len(v) == EMBEDDING_DIM for v in vectors)
    # 코사인 검색을 쓰므로 정규화돼 있어야 한다.
    norm = sum(v * v for v in vectors[0]) ** 0.5
    assert 0.99 < norm < 1.01
    # 서로 다른 텍스트는 다른 벡터여야 한다.
    assert vectors[0] != vectors[1]

    query = await embedder.embed_query("개발자 채용")
    assert len(query) == EMBEDDING_DIM
