"""Null external memory providers.

core/08 section 20 names ``NullExternalMemoryProvider`` and
``NullTemporalGraphProvider`` as initial implementations. They declare no
capabilities, return no candidates and decline ingestion -- which is exactly
what "Never4gA works without external memory" means in code.

``NativeMemoryProvider`` (core/08 section 5) is *not* a null provider and does
not belong here.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.capabilities import MemoryCapability, TemporalGraphCapability
from never4ga.domain.memory import IngestReceipt, MemoryCandidate, MemoryEpisode, MemoryQuery
from never4ga.ports.work_management import ProviderHealth

__all__ = ["NullExternalMemoryProvider", "NullTemporalGraphProvider"]


class NullExternalMemoryProvider:
    provider_id = "null-external-memory"

    @property
    def capabilities(self) -> frozenset[MemoryCapability]:
        return frozenset()

    def search(self, query: MemoryQuery) -> Sequence[MemoryCandidate]:
        return ()

    def ingest_episode(self, episode: MemoryEpisode) -> IngestReceipt:
        return IngestReceipt(
            provider_id=self.provider_id,
            accepted=False,
            detail="no external memory provider is configured",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, detail="no external memory provider is configured")


class NullTemporalGraphProvider:
    provider_id = "null-temporal-graph"

    @property
    def capabilities(self) -> frozenset[TemporalGraphCapability]:
        return frozenset()

    def search(self, query: MemoryQuery) -> Sequence[MemoryCandidate]:
        return ()

    def ingest(self, episode: MemoryEpisode) -> IngestReceipt:
        return IngestReceipt(
            provider_id=self.provider_id,
            accepted=False,
            detail="no temporal graph provider is configured",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, detail="no temporal graph provider is configured")
