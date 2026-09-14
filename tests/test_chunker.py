# 본문 청크 분할 테스트

from __future__ import annotations

from app.domains.crawler.chunker import (
    CHUNK_MAX_CHARS,
    build_chunks,
    hash_chunk,
    normalize,
)
from app.domains.market.enums import ChunkSection

FULL_BODY = """[주요업무]
- 결제 서버 API 개발 및 운영
- 대용량 트래픽 처리

[자격요건]
- Python 3년 이상
- PostgreSQL 사용 경험

[우대사항]
- Kubernetes 운영 경험
- 결제 도메인 이해

복리후생
- 점심 식대 지원
- 자기계발비 연 200만원

전형절차
- 서류 → 1차 면접 → 2차 면접
"""


def sections(chunks) -> list[str]:
    return [c.section.value for c in chunks]


def test_splits_into_three_sections() -> None:
    chunks = build_chunks(FULL_BODY)
    assert sections(chunks) == ["responsibility", "required", "preferred"]


def test_welfare_and_process_are_excluded() -> None:
    joined = " ".join(c.content for c in build_chunks(FULL_BODY))
    assert "식대" not in joined
    assert "자기계발비" not in joined
    assert "서류" not in joined
    assert "결제 서버 API" in joined


def test_no_header_becomes_single_responsibility_chunk() -> None:
    chunks = build_chunks("파이썬으로 백엔드를 개발합니다. AWS 위에서 운영합니다.")
    assert len(chunks) == 1
    assert chunks[0].section is ChunkSection.RESPONSIBILITY
    assert chunks[0].seq == 0


def test_empty_description_yields_nothing() -> None:
    assert build_chunks(None) == []
    assert build_chunks("") == []
    assert build_chunks("   \n  ") == []


def test_only_welfare_yields_nothing() -> None:
    assert build_chunks("복리후생\n- 점심 제공\n- 야근 없음") == []


def test_long_section_splits_by_seq() -> None:
    line = "- 대용량 트래픽을 처리하는 결제 서버를 개발하고 운영합니다.\n"
    body = "[주요업무]\n" + line * 120

    chunks = build_chunks(body)
    assert len(chunks) > 1
    assert all(c.section is ChunkSection.RESPONSIBILITY for c in chunks)
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert all(len(c.content) <= CHUNK_MAX_CHARS for c in chunks)


def test_line_longer_than_limit_is_force_split() -> None:
    body = "[주요업무]\n" + ("가" * (CHUNK_MAX_CHARS * 2 + 100))
    chunks = build_chunks(body)
    assert len(chunks) >= 2
    assert all(len(c.content) <= CHUNK_MAX_CHARS for c in chunks)


def test_hash_ignores_whitespace_only_changes() -> None:
    a = hash_chunk(ChunkSection.REQUIRED, "Python 3년   이상\n\n경험")
    b = hash_chunk(ChunkSection.REQUIRED, "Python 3년 이상 경험")
    assert a == b


def test_hash_changes_when_section_moves() -> None:
    text = "Kubernetes 운영 경험"
    assert hash_chunk(ChunkSection.REQUIRED, text) != hash_chunk(ChunkSection.PREFERRED, text)


def test_hash_is_stable_across_runs() -> None:
    first = build_chunks(FULL_BODY)
    second = build_chunks(FULL_BODY)
    assert [c.chunk_hash for c in first] == [c.chunk_hash for c in second]


def test_normalize_collapses_whitespace() -> None:
    assert normalize("  a \n\n b\t c  ") == "a b c"
