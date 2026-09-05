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

from app.domains.crawler.extractor import Section, split_sections
from app.domains.market.enums import ChunkSection

CHUNK_MAX_CHARS = 1200
CHUNK_MIN_CHARS = 40

_SECTION_MAP: dict[Section, ChunkSection] = {
    Section.RESPONSIBILITY: ChunkSection.RESPONSIBILITY,
    Section.REQUIRED: ChunkSection.REQUIRED,
    Section.PREFERRED: ChunkSection.PREFERRED,
    Section.BODY: ChunkSection.RESPONSIBILITY,
}

_WS = re.compile(r"\s+")
_BULLET_ONLY = re.compile(r"^[\s\-•·*▪◦]+$")


@dataclass(frozen=True)
class Chunk:
    section: ChunkSection
    seq: int
    content: str
    chunk_hash: str
    token_count: int


def normalize(text: str) -> str:
    return _WS.sub(" ", text).strip()


def hash_chunk(section: ChunkSection, content: str) -> str:
    return hashlib.sha256(f"{section.value}\x1f{normalize(content)}".encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, len(normalize(text)))


def build_chunks(description: str | None) -> list[Chunk]:
    if not description or not description.strip():
        return []

    merged: dict[ChunkSection, list[str]] = {}
    order: list[ChunkSection] = []
    for raw_section, body in split_sections(description):
        target = _SECTION_MAP.get(raw_section)
        if target is None:
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
        while len(line) > CHUNK_MAX_CHARS:
            flush()
            pieces.append(line[:CHUNK_MAX_CHARS])
            line = line[CHUNK_MAX_CHARS:]

        if size + len(line) + 1 > CHUNK_MAX_CHARS and current:
            flush()
        current.append(line)
        size += len(line) + 1

    flush()

    if len(pieces) > 1 and len(pieces[-1]) < CHUNK_MIN_CHARS:
        tail = pieces.pop()
        pieces[-1] = f"{pieces[-1]}\n{tail}"
    return pieces
