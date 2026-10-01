"""MemoryAugmentor -- optional external memory (core/08 section 4).

Mem0, Zep and future memory services implement this role. Never4gA's canonical
memory model (working, episodic, semantic, procedural) is unaffected by whether
one is configured; ``NullExternalMemoryProvider`` is a complete implementation.

No external adapter is built yet. core/08 section 20 requires the port to exist
early so that ContextAssembler, MemoryService and
SessionCloseout are not written with native-only assumptions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from never4ga.domain.capabilities import MemoryCapability
from never4ga.domain.memory import IngestReceipt, MemoryCandidate, MemoryEpisode, MemoryQuery
from never4ga.ports.work_management import ProviderHealth

__all__ = ["MemoryAugmentor"]


@runtime_checkable
class MemoryAugmentor(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[MemoryCapability]: ...

    def search(self, query: MemoryQuery) -> Sequence[MemoryCandidate]:
        """Return candidates. An unavailable provider returns nothing rather than
        breaking ordinary work (core/08 section 14)."""
        ...

    def ingest_episode(self, episode: MemoryEpisode) -> IngestReceipt: ...

    def health(self) -> ProviderHealth: ...
