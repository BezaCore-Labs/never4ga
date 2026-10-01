"""VectorIndex -- semantic retrieval (core/06 sections 8-11).

``DisabledVectorIndex`` is the default implementation (core/05 section 21):
semantic retrieval is optional, and reads degrade to empty without it.

Two constraints shape this port:

- section 10 -- points carry their :class:`EmbeddingNamespace`, so vectors from
  different models never mix and re-embedding is a namespace change;
- section 11 -- ``vector_space`` exists from the start, so ``embedding`` never
  becomes a singular permanent field.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from never4ga.domain.capabilities import VectorIndexCapability
from never4ga.domain.chunk import ChunkIdentity
from never4ga.domain.identity import ConceptId
from never4ga.domain.retrieval import RetrievalCandidate
from never4ga.ports.embedding_provider import EmbeddingNamespace, EmbeddingVector

__all__ = ["DEFAULT_VECTOR_SPACE", "VectorIndex", "VectorPoint", "VectorQuery"]

DEFAULT_VECTOR_SPACE = "semantic_dense"


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """One vector for one chunk in one namespace and vector space."""

    chunk: ChunkIdentity
    namespace: EmbeddingNamespace
    vector: EmbeddingVector
    vector_space: str = DEFAULT_VECTOR_SPACE
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.vector_space.strip():
            raise ValueError("vector_space is required")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class VectorQuery:
    """A backend-neutral nearest-neighbour request.

    There is no provider filter syntax here: core/06 section 25 Test G forbids
    requiring callers to write Qdrant filters or pgvector SQL.
    """

    namespace: EmbeddingNamespace
    vector: EmbeddingVector
    vector_space: str = DEFAULT_VECTOR_SPACE
    concept_ids: tuple[ConceptId, ...] = ()
    limit: int | None = None


@runtime_checkable
class VectorIndex(Protocol):
    @property
    def enabled(self) -> bool:
        """False when semantic retrieval is unavailable; reads then degrade to empty."""
        ...

    @property
    def capabilities(self) -> frozenset[VectorIndexCapability]: ...

    def upsert(self, points: Sequence[VectorPoint]) -> None: ...

    def remove_document(self, concept_id: ConceptId) -> None: ...

    def search(self, query: VectorQuery) -> Sequence[RetrievalCandidate]: ...

    def stored_keys(
        self,
        namespace: EmbeddingNamespace,
        vector_space: str = DEFAULT_VECTOR_SPACE,
    ) -> frozenset[str]:
        """Which chunk keys already have a vector here.

        What makes an *embedding backlog* computable without a second bookkeeping
        table: the chunks are known, and this says which of them are done. A
        namespace change therefore needs no special case, because everything is
        outstanding under a namespace nothing was stored in.
        """
        ...

    def clear(self) -> None: ...
