"""External memory domain types.

core/08: an external memory system is augmentation, projection and candidate
source -- never canonical durable truth. These types encode that:

- a candidate carries an :class:`ExternalId`, and ``concept_id`` stays ``None``
  until the candidate is promoted into canonical Markdown (sections 6, 7, 15);
- provider confidence remains provider metadata and is never read as Never4gA
  authority (section 6);
- ingestion is scoped to an explicit workspace and kind rather than streaming
  the vault or raw transcripts (sections 8, 9).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from never4ga.domain.identity import ConceptId, ExternalId

__all__ = ["IngestReceipt", "MemoryCandidate", "MemoryEpisode", "MemoryQuery"]


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """A provider-neutral memory request."""

    terms: tuple[str, ...] = ()
    workspace_id: ConceptId | None = None
    entities: tuple[str, ...] = ()
    valid_at: datetime | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """One normalised result from an external memory or temporal-graph provider."""

    provider: str
    external_id: ExternalId
    text: str
    entities: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    provider_score: float | None = None
    provider_confidence: float | None = None
    provenance: str = ""
    #: Set only once the candidate has been promoted to canonical Markdown.
    concept_id: ConceptId | None = None

    def __post_init__(self) -> None:
        if self.external_id.provider != self.provider:
            raise ValueError(
                f"candidate provider {self.provider!r} disagrees with external id "
                f"namespace {self.external_id.provider!r}"
            )


@dataclass(frozen=True, slots=True)
class MemoryEpisode:
    """Material offered to an external provider.

    core/08 section 9 prefers checkpoints and closeouts over raw chat turns, so
    the episode is a curated unit with an explicit workspace scope.
    """

    workspace_id: ConceptId
    kind: str
    text: str
    occurred_at: datetime | None = None
    entities: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("episode kind is required")
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))


@dataclass(frozen=True, slots=True)
class IngestReceipt:
    """The outcome of offering an episode to a provider."""

    provider_id: str
    accepted: bool
    external_id: ExternalId | None = None
    detail: str = ""
