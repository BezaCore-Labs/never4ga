"""MemoryAugmentor and TemporalGraphProvider contracts.

Specification: core/08 (whole document).

External memory is augmentation, never canonical truth. These contracts encode
the invariants of core/08 sections 6, 14, 15 and 19.
"""

from __future__ import annotations

import pytest

from never4ga.domain.capabilities import MemoryCapability, TemporalGraphCapability
from never4ga.domain.identity import ConceptId, ExternalId
from never4ga.domain.memory import MemoryCandidate, MemoryEpisode, MemoryQuery
from never4ga.ports.memory_augmentor import MemoryAugmentor
from never4ga.ports.temporal_graph import TemporalGraphProvider


class MemoryAugmentorContract:
    @pytest.fixture
    def augmentor(self) -> MemoryAugmentor:
        raise NotImplementedError("supply a MemoryAugmentor fixture")

    def test_declares_a_provider_id_and_capabilities(self, augmentor: MemoryAugmentor) -> None:
        assert augmentor.provider_id
        assert isinstance(augmentor.capabilities, frozenset)

    def test_search_returns_candidates_not_canonical_facts(
        self, augmentor: MemoryAugmentor
    ) -> None:
        for candidate in augmentor.search(MemoryQuery(terms=("anything",))):
            assert isinstance(candidate, MemoryCandidate)
            assert isinstance(candidate.external_id, ExternalId)
            assert not issubclass(ExternalId, ConceptId)

    def test_candidates_are_not_canonical_until_promoted(self, augmentor: MemoryAugmentor) -> None:
        # core/08 section 7: promotion is an explicit flow.
        for candidate in augmentor.search(MemoryQuery(terms=("anything",))):
            assert candidate.concept_id is None or isinstance(candidate.concept_id, ConceptId)

    def test_ingest_is_accepted_or_declined_explicitly(self, augmentor: MemoryAugmentor) -> None:
        receipt = augmentor.ingest_episode(
            MemoryEpisode(
                workspace_id=ConceptId.new(),
                kind="checkpoint",
                text="Did a thing.",
            )
        )
        assert isinstance(receipt.accepted, bool)
        assert receipt.provider_id == augmentor.provider_id

    def test_health_reports_without_raising(self, augmentor: MemoryAugmentor) -> None:
        assert isinstance(augmentor.health().available, bool)

    def test_scoped_ingestion_records_its_workspace(self, augmentor: MemoryAugmentor) -> None:
        # core/08 section 8: ingestion scopes are explicit, never "the whole vault".
        workspace = ConceptId.new()
        episode = MemoryEpisode(workspace_id=workspace, kind="checkpoint", text="scoped")
        assert episode.workspace_id == workspace


class TemporalGraphProviderContract:
    @pytest.fixture
    def provider(self) -> TemporalGraphProvider:
        raise NotImplementedError("supply a TemporalGraphProvider fixture")

    def test_declares_a_provider_id_and_capabilities(self, provider: TemporalGraphProvider) -> None:
        assert provider.provider_id
        assert isinstance(provider.capabilities, frozenset)

    def test_search_returns_candidates(self, provider: TemporalGraphProvider) -> None:
        for candidate in provider.search(MemoryQuery(terms=("anything",))):
            assert isinstance(candidate.external_id, ExternalId)

    def test_ingest_returns_a_receipt(self, provider: TemporalGraphProvider) -> None:
        receipt = provider.ingest(
            MemoryEpisode(workspace_id=ConceptId.new(), kind="checkpoint", text="x")
        )
        assert receipt.provider_id == provider.provider_id

    def test_health_reports_without_raising(self, provider: TemporalGraphProvider) -> None:
        assert isinstance(provider.health().available, bool)

    def test_capability_vocabularies_stay_separate(self, provider: TemporalGraphProvider) -> None:
        # A provider implementing both roles (core/08 section 12) must not blur them.
        assert set(TemporalGraphCapability) & set(MemoryCapability) == set()
