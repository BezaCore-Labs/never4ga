"""TextIndex -- lexical retrieval (core/07 Stage D).

The v0.1 implementation is SQLite FTS5 (core/05 section 21), but core/06
section 18 is explicit: "Never4gA query semantics must not expose raw FTS5
syntax as its only public API". :class:`TextQuery` is therefore a structured
request -- separate term, phrase and exact-identifier fields -- that any lexical
engine can render into its own syntax.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Protocol, runtime_checkable

from never4ga.domain.capabilities import TextIndexCapability
from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.retrieval import RetrievalCandidate

__all__ = ["FIELD_KEYWORDS", "FIELD_TITLE", "IndexedChunk", "TextIndex", "TextQuery"]

#: Optional document-level fields a chunk may carry in :attr:`IndexedChunk.metadata`.
#:
#: details/data-indexing-maintenance.md section 7 lists title, description,
#: aliases and selected tags among the material a lexical index should hold, and
#: none of them belongs to a chunk: they belong to the document the chunk came
#: from. Rather than teach every engine to join against a metadata projection it
#: may not share a database with, the indexing pipeline repeats them per chunk
#: here, and an engine that can weight fields separately does so.
#:
#: Both are optional. An engine that ignores them is still a valid TextIndex.
FIELD_TITLE: Final = "title"
FIELD_KEYWORDS: Final = "keywords"


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    """A deterministically identified chunk offered to a lexical index."""

    chunk: ChunkIdentity
    path: VaultPath
    text: str
    line_range: LineRange
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class TextQuery:
    """A backend-neutral lexical request.

    ``terms``
        Free tokens, matched case-insensitively.
    ``phrases``
        Contiguous multi-token sequences.
    ``exact_identifiers``
        Whole-token identifiers such as ``ADR-0002`` or ``OP #431``. These must
        match exactly, never as substrings, and are reported with the
        ``exact_identifier_match`` reason (core/07 section 16).
    ``concept_ids``
        Restricts the search to an already-reduced candidate scope
        (core/07 section 6). A chunk owned by a path belongs to no concept,
        so a scope excludes it.
    ``include_paths``
        Admits path-owned chunks (core/02 section 3.3) *as well as* the scope.
        A pile belongs to no workspace, so reducing to one workspace's
        concepts would otherwise shut it out of every focused pack. Changes
        nothing when ``concept_ids`` is empty, which already searches all.
    """

    terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    exact_identifiers: tuple[str, ...] = ()
    concept_ids: tuple[ConceptId, ...] = ()
    include_paths: bool = False
    limit: int | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.terms or self.phrases or self.exact_identifiers)


@runtime_checkable
class TextIndex(Protocol):
    @property
    def capabilities(self) -> frozenset[TextIndexCapability]: ...

    def index_chunk(self, chunk: IndexedChunk) -> None:
        """Add or replace one chunk. Re-indexing the same chunk must not duplicate it."""
        ...

    def chunk_keys(self) -> frozenset[str]:
        """Every chunk key currently indexed.

        Cheap, and that is the point: it is what lets an embedding backlog be
        counted without re-reading the vault. `health()` is called by `doctor`,
        by `index --status`, and by the CLI before every command that might
        delegate to a service.
        """
        ...

    def remove_document(self, concept_id: ConceptId) -> None: ...

    def remove_path(self, path: VaultPath) -> None:
        """Remove every chunk owned by ``path`` (core/02 section 3.3).

        Only path-owned chunks: a concept's chunks are its own whatever path
        it sits at, and `remove_document` is what removes those. Missing is
        not an error; the end state is the same.
        """
        ...

    def search(self, query: TextQuery) -> Sequence[RetrievalCandidate]: ...

    def clear(self) -> None: ...
