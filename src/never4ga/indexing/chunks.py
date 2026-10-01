"""Deterministic chunks of a canonical document.

details/data-indexing-maintenance.md section 6 fixes the order: document, then
heading sections, then paragraph boundaries when a section is too large. "Do not
start with blind fixed-length token windows." Overlap is minimal -- none, until
an evaluation shows it helps.

core/06 section 4 fixes identity: document UUID, normalised structural locator,
chunk ordinal within that locator, content hash, chunking-policy version. The
embedding model is deliberately absent -- re-embedding must not re-key a chunk.

The ordinal being scoped to the *heading path* rather than to the document is
what makes identity survive editing. Fix a typo in one section and only that
section's chunks change; every other chunk keeps its key, and with it every
vector and FTS row derived from it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.document import ForeignNote, StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.indexing.markdown import Section, scan_sections
from never4ga.ports.text_index import IndexedChunk

__all__ = ["CHUNK_POLICY_VERSION", "MAX_CHUNK_CHARACTERS", "build_chunks", "build_foreign_chunks"]

#: Part of chunk identity (core/06 section 4). Changing how chunks are cut
#: changes this string, which re-keys every chunk and forces a rebuild -- which
#: is the point: an old chunk key must never silently mean something new.
CHUNK_POLICY_VERSION: Final = "chunk/0.2"

#: Characters, not tokens. Never4gA owns no tokenizer and will not acquire one
#: to draw a boundary; characters are deterministic, stdlib and reproducible on
#: any machine.
#:
#: Lexical retrieval is not sensitive to the exact number. Where a cut is
#: allowed matters more; see :func:`_paragraphs`.
MAX_CHUNK_CHARACTERS: Final = 2_000

#: A Markdown list item: a hyphen, asterisk or plus, or `1.` / `1)`, under four
#: spaces of indent. Deliberately not a Markdown parser -- this decides where a
#: cut is *allowed*, and being wrong about an exotic bullet costs one oversized
#: chunk rather than a wrong answer.
_LIST_ITEM: Final = re.compile(r"^ {0,3}(?:[-*+][ \t]|\d+[.)][ \t])")


def build_chunks(document: StoredDocument) -> tuple[IndexedChunk, ...]:
    """Every chunk of one document, in document order.

    Line ranges are file-relative: the frontmatter block is not part of the body,
    but the person opening the file counts from line 1, so ``body_offset`` is
    added. It affects only what a chunk *reports*, never what it *is* -- chunk
    identity is the heading path, the ordinal and the content hash, so a longer
    frontmatter block does not re-key a single chunk.
    """
    return _build(document.concept_id, document.path, document.body, document.body_offset)


def build_foreign_chunks(note: ForeignNote) -> tuple[IndexedChunk, ...]:
    """Every chunk of a foreign note, owned by its path (core/01 section 1).

    The same policy as a concept's, so the two rank alike; only the owner
    differs, and it is the path, never an identity.
    """
    return _build(None, note.path, note.body, note.body_offset)


def _build(
    concept_id: ConceptId | None, path: VaultPath, body: str, body_offset: int
) -> tuple[IndexedChunk, ...]:
    chunks: list[IndexedChunk] = []
    for section in scan_sections(body):
        for ordinal, (text, body_range) in enumerate(_split(section)):
            line_range = _shift(body_range, body_offset)
            chunks.append(
                IndexedChunk(
                    chunk=ChunkIdentity(
                        concept_id=concept_id,
                        heading_path=section.heading_path,
                        ordinal=ordinal,
                        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        policy_version=CHUNK_POLICY_VERSION,
                        source=path if concept_id is None else None,
                    ),
                    path=path,
                    text=text,
                    line_range=line_range,
                )
            )
    return tuple(chunks)


def _shift(line_range: LineRange, offset: int) -> LineRange:
    return LineRange(start=line_range.start + offset, end=line_range.end + offset)


def _split(section: Section) -> list[tuple[str, LineRange]]:
    """One section, as one chunk or as several cut at blank lines."""
    if len(section.text) <= MAX_CHUNK_CHARACTERS:
        return [(section.text, section.line_range)]

    chunks: list[tuple[str, LineRange]] = []
    current: list[str] = []
    start = end = section.line_range.start
    for paragraph, first, last in _paragraphs(section):
        pending = len("\n\n".join([*current, paragraph]))
        if current and pending > MAX_CHUNK_CHARACTERS:
            chunks.append(("\n\n".join(current), LineRange(start=start, end=end)))
            current, start = [], first
        current.append(paragraph)
        end = last
    if current:
        chunks.append(("\n\n".join(current), LineRange(start=start, end=end)))
    return chunks


def _paragraphs(section: Section) -> list[tuple[str, int, int]]:
    """Blank-line-separated blocks, each with its own line range.

    A block longer than the budget is cut at list-item boundaries if it has any,
    and left whole if it does not. Both halves matter.

    **Why cut lists.** `_paragraphs` splits on blank lines, so a Markdown list
    written without blank lines between its items is one paragraph however long
    it runs. Session logs and inventories are commonly written that way, and
    they are where oversized blocks come from.

    **Why not cut prose.** A sentence has no boundary to land on, and an
    arbitrary cut mid-clause makes a chunk that is wrong rather than merely
    large. An oversized prose chunk is a worse retrieval unit but an honest
    one.
    """
    blocks: list[tuple[str, int, int]] = []
    lines: list[str] = []
    first = section.line_range.start
    for offset, line in enumerate(section.text.splitlines()):
        number = section.line_range.start + offset
        if line.strip():
            if not lines:
                first = number
            lines.append(line)
            continue
        if lines:
            blocks.append(("\n".join(lines), first, number - 1))
            lines = []
    if lines:
        blocks.append(("\n".join(lines), first, section.line_range.end))
    divided = [piece for block in blocks for piece in _divide_at_list_items(block)]
    return _keep_the_heading_attached(section, divided)


def _divide_at_list_items(block: tuple[str, int, int]) -> list[tuple[str, int, int]]:
    """One blank-line-free block, cut before the list items that overrun the budget.

    A block with no list items comes back whole, and so does one that fits.
    """
    text, first, last = block
    if len(text) <= MAX_CHUNK_CHARACTERS:
        return [block]

    lines = text.splitlines()
    pieces: list[tuple[str, int, int]] = []
    current: list[str] = []
    start = first
    for offset, line in enumerate(lines):
        number = first + offset
        pending = len("\n".join([*current, line]))
        if current and pending > MAX_CHUNK_CHARACTERS and _LIST_ITEM.match(line):
            pieces.append(("\n".join(current), start, number - 1))
            current, start = [], number
        current.append(line)
    if current:
        pieces.append(("\n".join(current), start, last))
    return pieces


def _keep_the_heading_attached(
    section: Section, blocks: list[tuple[str, int, int]]
) -> list[tuple[str, int, int]]:
    """Never let a heading become a chunk of its own.

    A blank line after a heading makes it a block by itself, and a chunk whose
    entire content is ``## Two`` retrieves nothing and explains nothing. It
    belongs to the prose it introduces.
    """
    if not section.heading_path or len(blocks) < 2:
        return blocks
    text, first, last = blocks[0]
    if first != section.line_range.start or last != first:
        return blocks
    following, _, following_last = blocks[1]
    return [(f"{text}\n\n{following}", first, following_last), *blocks[2:]]
