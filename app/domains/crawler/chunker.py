# 공고 본문을 임베딩용 섹션 청크로 분할

import hashlib
import re
from dataclasses import dataclass

from app.domains.crawler.extractor import Section, split_sections
from app.domains.crawler.enums import ChunkSection
from typing import Optional


@dataclass(frozen=True)
class Chunk:
    section: ChunkSection
    seq: int
    content: str
    chunk_hash: str
    token_count: int


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def hash_chunk(section: ChunkSection, content: str) -> str:
    return hashlib.sha256(f"{section.value}\x1f{normalize(content)}".encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, len(normalize(text)))


def build_chunks(description: Optional[str]) -> list[Chunk]:
    if not description or not description.strip():
        return []

    section_map: dict[Section, ChunkSection] = {
        Section.RESPONSIBILITY: ChunkSection.RESPONSIBILITY,
        Section.REQUIRED: ChunkSection.REQUIRED,
        Section.PREFERRED: ChunkSection.PREFERRED,
        Section.BODY: ChunkSection.RESPONSIBILITY,
    }
    merged: dict[ChunkSection, list[str]] = {}
    order: list[ChunkSection] = []
    for raw_section, body in split_sections(description):
        target = section_map.get(raw_section)
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


def _split_long(text: str) -> list[str]:
    max_chars = 1200
    if len(text) <= max_chars:
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
        if re.match(r"^[\s\-•·*▪◦]+$", raw_line):
            continue
        line = raw_line
        while len(line) > max_chars:
            flush()
            pieces.append(line[:max_chars])
            line = line[max_chars:]

        if size + len(line) + 1 > max_chars and current:
            flush()
        current.append(line)
        size += len(line) + 1

    flush()

    if len(pieces) > 1 and len(pieces[-1]) < 40:
        tail = pieces.pop()
        pieces[-1] = f"{pieces[-1]}\n{tail}"
    return pieces
