"""TemporalGraphProvider -- optional evolving-fact provider (core/08 section 4).

Graphiti and Zep's Context Graph are the candidate implementations. The role is
kept separate from :class:`~never4ga.ports.memory_augmentor.MemoryAugmentor`
because core/08 section 4 warns against forcing every memory product into one
false abstraction, even though one provider may implement both.

Only ``search``, ``ingest`` and ``health`` are defined. ``neighbors``, ``facts``,
``history`` and ``valid_at`` are listed in the specification but are not added
until a provider evaluation needs them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from never4ga.domain.capabilities import TemporalGraphCapability
from never4ga.domain.memory import IngestReceipt, MemoryCandidate, MemoryEpisode, MemoryQuery
from never4ga.ports.work_management import ProviderHealth

__all__ = ["TemporalGraphProvider"]


@runtime_checkable
class TemporalGraphProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[TemporalGraphCapability]: ...

    def search(self, query: MemoryQuery) -> Sequence[MemoryCandidate]: ...

    def ingest(self, episode: MemoryEpisode) -> IngestReceipt: ...

    def health(self) -> ProviderHealth: ...
