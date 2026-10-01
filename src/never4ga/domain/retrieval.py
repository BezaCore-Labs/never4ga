"""Backend-neutral retrieval candidates.

core/06 section 6: every retriever -- lexical, vector, graph, external memory --
returns the same normalised candidate shape, so that fusion (section 7) lives in
Never4gA rather than inside any one engine.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from never4ga.domain.chunk import ChunkIdentity
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason

__all__ = ["RetrievalCandidate"]


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    """One candidate produced by one retriever.

    ``provider_score`` is diagnostic only. core/06 section 6 forbids assuming
    scores from different engines are numerically comparable, so this class
    intentionally defines no ordering: fusion must work from ``rank``.
    """

    #: ``None`` for a chunk of a foreign note (core/01 section 1), which has
    #: no concept; ``source_path`` then says what it came from, and is required.
    concept_id: ConceptId | None
    retriever: str
    rank: int
    reason: AcquisitionReason
    chunk: ChunkIdentity | None = None
    source_path: VaultPath | None = None
    provider_score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.concept_id is None and self.source_path is None:
            raise ValueError("a candidate with no concept_id needs a source_path")
        if self.rank < 1:
            raise ValueError(f"candidate rank is 1-based, got {self.rank}")
        if not self.retriever.strip():
            raise ValueError("candidate retriever is required")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def candidate_id(self) -> str:
        """Deterministic identity of this candidate within a retrieval run."""
        digest = hashlib.sha256()
        owner = str(self.concept_id) if self.concept_id is not None else f"path:{self.source_path}"
        for part in (
            self.retriever,
            owner,
            self.chunk.key if self.chunk is not None else "",
        ):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x1f")
        return digest.hexdigest()
