"""In-memory MemoryAugmentor.

It exists so the external-memory contract runs against a provider that actually
returns candidates -- a null provider cannot demonstrate that provider identity
stays separate from canonical identity (core/08 sections 6 and 15).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from never4ga.domain.capabilities import MemoryCapability
from never4ga.domain.identity import ExternalId
from never4ga.domain.memory import IngestReceipt, MemoryCandidate, MemoryEpisode, MemoryQuery
from never4ga.ports.work_management import ProviderHealth

__all__ = ["FakeMemoryAugmentor"]


class FakeMemoryAugmentor:
    provider_id = "fake-memory"

    def __init__(self, candidates: Sequence[MemoryCandidate] = ()) -> None:
        self._candidates = list(candidates)
        self._episodes: list[MemoryEpisode] = []
        self._counter = itertools.count(1)

    @property
    def capabilities(self) -> frozenset[MemoryCapability]:
        return frozenset({MemoryCapability.SEARCH, MemoryCapability.EPISODIC_INGEST})

    @property
    def ingested(self) -> Sequence[MemoryEpisode]:
        return tuple(self._episodes)

    def search(self, query: MemoryQuery) -> Sequence[MemoryCandidate]:
        matches = [
            candidate
            for candidate in self._candidates
            if not query.terms
            or any(term.casefold() in candidate.text.casefold() for term in query.terms)
        ]
        if query.workspace_id is not None:
            # A provider that cannot scope by workspace must not pretend it can;
            # this one stores no workspace, so scoping is the caller's job.
            pass
        if query.limit is not None:
            matches = matches[: query.limit]
        return tuple(matches)

    def ingest_episode(self, episode: MemoryEpisode) -> IngestReceipt:
        self._episodes.append(episode)
        external_id = ExternalId(provider=self.provider_id, value=f"ep-{next(self._counter)}")
        return IngestReceipt(provider_id=self.provider_id, accepted=True, external_id=external_id)

    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True)
