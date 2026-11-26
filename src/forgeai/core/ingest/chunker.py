"""Heading-aware deterministic document chunker with provenance & injection flagging."""

from __future__ import annotations

import re

from forgeai.core.schemas import DocumentChunk
from forgeai.security.sanitize import detect_prompt_injection

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def chunk_document(
    source: str,
    text: str,
    *,
    chunk_size: int = 900,
    chunk_overlap: int = 150,
) -> list[DocumentChunk]:
    """Split document text into overlapping chunks preserving markdown headings and offsets."""
    cleaned = text.strip()
    if not cleaned:
        return []

    overlap = min(chunk_overlap, max(0, chunk_size // 2))
    sections = _split_by_headings(cleaned)
    chunks: list[DocumentChunk] = []
    chunk_idx = 0

    for section_title, section_text, section_offset in sections:
        if len(section_text) <= chunk_size:
            chunk_idx += 1
            chunks.append(
                _make_chunk(
                    chunk_idx=chunk_idx,
                    source=source,
                    section=section_title,
                    text=section_text,
                    char_start=section_offset,
                    char_end=section_offset + len(section_text),
                )
            )
            continue

        start = 0
        length = len(section_text)
        while start < length:
            end = min(length, start + chunk_size)
            if end < length:
                # Prefer breaking on paragraph or sentence boundary
                newline_pos = section_text.rfind("\n", start + chunk_size // 2, end)
                if newline_pos != -1:
                    end = newline_pos
                else:
                    space_pos = section_text.rfind(" ", start + chunk_size // 2, end)
                    if space_pos != -1:
                        end = space_pos

            piece = section_text[start:end].strip()
            if piece:
                chunk_idx += 1
                chunks.append(
                    _make_chunk(
                        chunk_idx=chunk_idx,
                        source=source,
                        section=section_title,
                        text=piece,
                        char_start=section_offset + start,
                        char_end=section_offset + end,
                    )
                )
            if end >= length:
                break
            start = max(start + 1, end - overlap)

    return chunks


def _split_by_headings(text: str) -> list[tuple[str, str, int]]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("document", text, 0)]

    sections: list[tuple[str, str, int]] = []
    first_start = matches[0].start()
    if first_start > 0:
        preamble = text[:first_start].strip()
        if preamble:
            sections.append(("preamble", preamble, 0))

    for i, match in enumerate(matches):
        title = match.group(2).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((title, body, start))
    return sections


def _make_chunk(
    *,
    chunk_idx: int,
    source: str,
    section: str,
    text: str,
    char_start: int,
    char_end: int,
) -> DocumentChunk:
    scan = detect_prompt_injection(text)
    return DocumentChunk(
        chunk_id=f"c{chunk_idx:03d}",
        source=source,
        section=section,
        text=text,
        char_start=char_start,
        char_end=char_end,
        injection_flagged=scan.flagged,
        metadata={"injection_matches": scan.matches} if scan.flagged else {},
    )
