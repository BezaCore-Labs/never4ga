"""In-memory TextIndex.

Ranking is intentionally naive -- term frequency, with exact identifier matches
placed first. It exists to prove that higher-level code can run against *any*
lexical engine, not to approximate BM25. Real ranking is SQLite FTS5's, in
``SQLiteFTS5Index``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from never4ga.domain.capabilities import TextIndexCapability
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.retrieval import RetrievalCandidate
from never4ga.ports.text_index import IndexedChunk, TextQuery

__all__ = ["InMemoryTextIndex"]

_TOKEN = re.compile(r"[^\W_]+(?:[-#][^\W_]+)*", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN.finditer(text)]


class InMemoryTextIndex:
    def __init__(self) -> None:
        self._chunks: dict[str, IndexedChunk] = {}
        self._tokens: dict[str, list[str]] = {}

    @property
    def capabilities(self) -> frozenset[TextIndexCapability]:
        return frozenset(
            {
                TextIndexCapability.EXACT_IDENTIFIERS,
                TextIndexCapability.PHRASE_TERMS,
                TextIndexCapability.SCOPED_FILTERING,
            }
        )

    def index_chunk(self, chunk: IndexedChunk) -> None:
        self._chunks[chunk.chunk.key] = chunk
        self._tokens[chunk.chunk.key] = _tokenize(chunk.text)

    def chunk_keys(self) -> frozenset[str]:
        return frozenset(self._chunks)

    def remove_document(self, concept_id: ConceptId) -> None:
        for key in [k for k, c in self._chunks.items() if c.chunk.concept_id == concept_id]:
            del self._chunks[key]
            del self._tokens[key]

    def remove_path(self, path: VaultPath) -> None:
        owned = [
            k
            for k, c in self._chunks.items()
            if c.chunk.concept_id is None and c.chunk.source == path
        ]
        for key in owned:
            del self._chunks[key]
            del self._tokens[key]

    def search(self, query: TextQuery) -> Sequence[RetrievalCandidate]:
        if query.is_empty:
            return ()

        scored: list[tuple[bool, float, IndexedChunk]] = []
        for key, chunk in self._chunks.items():
            owner = chunk.chunk.concept_id
            if query.concept_ids and (
                (owner is None and not query.include_paths)
                or (owner is not None and owner not in query.concept_ids)
            ):
                # A scope is a set of concepts; a chunk that belongs to no
                # concept is outside every one of them unless paths are asked
                # for as well.
                continue
            tokens = self._tokens[key]
            exact = any(identifier.casefold() in tokens for identifier in query.exact_identifiers)
            score = float(sum(tokens.count(term.casefold()) for term in query.terms))
            haystack = chunk.text.casefold()
            score += float(sum(haystack.count(phrase.casefold()) for phrase in query.phrases))
            if exact or score > 0:
                scored.append((exact, score, chunk))

        # Exact identifier matches first (core/07 section 16 "Exact decision"),
        # then term frequency, then chunk key for deterministic ties.
        scored.sort(key=lambda row: (not row[0], -row[1], row[2].chunk.key))
        if query.limit is not None:
            scored = scored[: query.limit]

        return tuple(
            RetrievalCandidate(
                concept_id=chunk.chunk.concept_id,
                retriever="text",
                rank=position,
                reason=AcquisitionReason.of(
                    ReasonCode.EXACT_IDENTIFIER_MATCH if exact else ReasonCode.FTS_RANK,
                    detail=f"rank {position}",
                ),
                chunk=chunk.chunk,
                source_path=chunk.path,
                provider_score=score,
                metadata=chunk.metadata,
            )
            for position, (exact, score, chunk) in enumerate(scored, start=1)
        )

    def clear(self) -> None:
        self._chunks.clear()
        self._tokens.clear()
