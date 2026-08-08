"""본문 → 섹션 청크 분할 (임베딩 단위).

    responsibility   주요업무
    required         자격요건
    preferred        우대사항

규칙
    - 복지 · 전형절차 · 회사소개 상용구는 제외 (검색 노이즈)
    - 섹션 헤더를 못 찾으면 전체를 responsibility 단일 청크로
    - 섹션이 길면 seq 를 늘려 분할. 토큰 상한은 임베딩 모델 기준
    - 각 청크마다 chunk_hash(정규화 텍스트 sha256) 계산 → 변경분만 재임베딩
    - body_is_image=true 또는 description IS NULL 인 공고는 대상 아님

섹션 판정은 extractor.split_sections 를 그대로 쓴다. 스킬 추출과 임베딩이
서로 다른 기준으로 본문을 자르면 "자격요건에 있다" 는 근거가 두 곳에서
달라진다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.core.enums import ChunkSection
from app.domains.crawler.extractor import Section, split_sections

# 청크 1개의 최대 길이(문자). 한국어는 대략 1자 ≈ 0.7~1 토큰이라 1200자면
# 넉넉히 1k 토큰 안쪽이다. 96개를 한 번에 보내도 배치 토큰 상한에 걸리지
# 않는 크기로 잡았다.
CHUNK_MAX_CHARS = 1200
# 이보다 짧은 꼬리 조각은 앞 청크에 붙인다. "우대사항\n- 없음" 같은 파편이
# 독립 청크가 되면 검색 결과에 의미 없는 히트를 만든다.
CHUNK_MIN_CHARS = 40

# 임베딩 대상 섹션 매핑. IGNORE(복지 · 전형절차 · 회사소개)는 의도적으로
# 빠져 있다 — 어느 회사나 비슷해서 벡터 공간에서 전부 붙어 버린다.
_SECTION_MAP: dict[Section, ChunkSection] = {
    Section.RESPONSIBILITY: ChunkSection.RESPONSIBILITY,
    Section.REQUIRED: ChunkSection.REQUIRED,
    Section.PREFERRED: ChunkSection.PREFERRED,
    # 헤더 이전 도입부. 헤더가 아예 없으면 본문 전체가 여기로 온다.
    Section.BODY: ChunkSection.RESPONSIBILITY,
}

_WS = re.compile(r"\s+")
# 목록 기호만 남은 줄. 청크 경계 계산에서 노이즈다.
_BULLET_ONLY = re.compile(r"^[\s\-•·*▪◦]+$")


@dataclass(frozen=True)
class Chunk:
    """임베딩 1건. posting_chunk 한 행에 대응한다."""

    section: ChunkSection
    seq: int
    content: str
    chunk_hash: str
    token_count: int


def normalize(text: str) -> str:
    """해시 계산용 정규화. 공백 차이만으로 재임베딩이 걸리지 않게 한다."""
    return _WS.sub(" ", text).strip()


def hash_chunk(section: ChunkSection, content: str) -> str:
    """섹션까지 해시에 넣는다.

    같은 문장이 자격요건에서 우대사항으로 옮겨간 경우도 '변경' 이다.
    내용만 해싱하면 섹션 이동을 놓친다.
    """
    return hashlib.sha256(f"{section.value}\x1f{normalize(content)}".encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    """토큰 수 근사치. 정확한 값은 API 응답에만 있고 여기서는 참고용이다.

    용도가 '너무 큰 청크 감지' 라 과대 추정되는 쪽이 안전하다.
    """
    return max(1, len(normalize(text)))


def build_chunks(description: str | None) -> list[Chunk]:
    """공고 본문을 임베딩 청크 목록으로 자른다.

    description 이 비어 있으면 빈 리스트. body_is_image · body_extract_failed
    필터는 호출부(embed_service)가 SQL 단계에서 먼저 건다.
    """
    if not description or not description.strip():
        return []

    # 사이트가 헤더를 반복하면 같은 섹션이 여러 번 나온다. 순서를 지켜 잇는다.
    merged: dict[ChunkSection, list[str]] = {}
    order: list[ChunkSection] = []
    for raw_section, body in split_sections(description):
        target = _SECTION_MAP.get(raw_section)
        if target is None:  # IGNORE
            continue
        text = body.strip()
        if not text:
            continue
        if target not in merged:
            merged[target] = []
            order.append(target)
        merged[target].append(text)

    chunks: list[Chunk] = []
    for section in order:
        body = "\n".join(merged[section]).strip()
        for seq, piece in enumerate(_split_long(body)):
            chunks.append(
                Chunk(
                    section=section,
                    seq=seq,
                    content=piece,
                    chunk_hash=hash_chunk(section, piece),
                    token_count=estimate_tokens(piece),
                )
            )
    return chunks


# ── 내부 ────────────────────────────────────────────────────────────────────
def _split_long(text: str) -> list[str]:
    """CHUNK_MAX_CHARS 를 넘으면 줄 단위로 쪼갠다.

    문장 중간을 자르지 않는다. 한 줄이 그 자체로 상한을 넘을 때만 강제로
    자른다(줄바꿈 없는 본문 — 드물다).
    """
    if len(text) <= CHUNK_MAX_CHARS:
        return [text]

    pieces: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        joined = "\n".join(current).strip()
        if joined:
            pieces.append(joined)
        current = []
        size = 0

    for raw_line in text.splitlines():
        if _BULLET_ONLY.match(raw_line):
            continue
        line = raw_line
        while len(line) > CHUNK_MAX_CHARS:  # 한 줄이 통째로 상한을 넘는 경우
            flush()
            pieces.append(line[:CHUNK_MAX_CHARS])
            line = line[CHUNK_MAX_CHARS:]

        if size + len(line) + 1 > CHUNK_MAX_CHARS and current:
            flush()
        current.append(line)
        size += len(line) + 1

    flush()

    # 마지막 조각이 너무 짧으면 앞에 붙인다.
    if len(pieces) > 1 and len(pieces[-1]) < CHUNK_MIN_CHARS:
        tail = pieces.pop()
        pieces[-1] = f"{pieces[-1]}\n{tail}"
    return pieces
