"""Run the external-system and memory contracts against the fakes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from never4ga.adapters.fakes import (
    FakeMemoryAugmentor,
    FakeWorkManagementProvider,
    FakeWorkManagementWriter,
    InMemorySecretStore,
)
from never4ga.adapters.null import NullExternalMemoryProvider, NullTemporalGraphProvider
from never4ga.domain.identity import ConceptId, ExternalId
from never4ga.domain.memory import MemoryCandidate, MemoryEpisode, MemoryQuery
from never4ga.ports.memory_augmentor import MemoryAugmentor
from never4ga.ports.secret_store import SecretStore
from never4ga.ports.temporal_graph import TemporalGraphProvider
from never4ga.ports.work_management import (
    WorkItem,
    WorkManagementProvider,
    WorkManagementWriter,
)
from tests.contracts.memory_contract import (
    MemoryAugmentorContract,
    TemporalGraphProviderContract,
)
from tests.contracts.secret_store_contract import SecretStoreContract
from tests.contracts.work_management_contract import (
    UnavailableWorkManagementProviderContract,
    WorkManagementProviderContract,
)
from tests.contracts.work_management_writer_contract import (
    RefusingWorkManagementWriterContract,
    WorkManagementWriterContract,
)

pytestmark = pytest.mark.contract

WORK_ITEM = WorkItem(
    ref=ExternalId(provider="fake-pm", value="431"),
    title="Implement mechanical scope resolution",
    status="in_progress",
    assignee="ada",
    updated_at=datetime(2026, 8, 22, 19, 45, tzinfo=UTC),
)


class TestFakeWorkManagementWriter(WorkManagementWriterContract):
    @pytest.fixture
    def writer(self) -> WorkManagementWriter:
        return FakeWorkManagementWriter([WORK_ITEM])

    @pytest.fixture
    def known_item(self, writer: WorkManagementWriter) -> WorkItem:
        return WORK_ITEM

    @pytest.fixture
    def project(self) -> str:
        return "never4ga"


class TestFakeReadOnlyWriter(RefusingWorkManagementWriterContract):
    @pytest.fixture
    def writer(self) -> WorkManagementWriter:
        return FakeWorkManagementWriter([WORK_ITEM], writable=False)

    @pytest.fixture
    def known_item(self, writer: WorkManagementWriter) -> WorkItem:
        return WORK_ITEM


class TestFakeWorkManagementProvider(WorkManagementProviderContract):
    def test_a_read_only_provider_is_not_a_writer(self) -> None:
        # What makes the two-Protocol split real rather than decorative: a
        # provider that reads and cannot write, which nothing forces to
        # implement writing (core/03 section 24).
        assert not isinstance(FakeWorkManagementProvider([WORK_ITEM]), WorkManagementWriter)
        assert isinstance(FakeWorkManagementWriter([WORK_ITEM]), WorkManagementWriter)

    @pytest.fixture
    def provider(self) -> WorkManagementProvider:
        return FakeWorkManagementProvider([WORK_ITEM])

    @pytest.fixture
    def known_item(self, provider: WorkManagementProvider) -> WorkItem:
        return WORK_ITEM


class TestOfflineWorkManagementProvider(UnavailableWorkManagementProviderContract):
    @pytest.fixture
    def provider(self) -> WorkManagementProvider:
        return FakeWorkManagementProvider([WORK_ITEM], available=False)


class TestInMemorySecretStore(SecretStoreContract):
    @pytest.fixture
    def store(self) -> SecretStore:
        return InMemorySecretStore()


class TestNullExternalMemoryProvider(MemoryAugmentorContract):
    @pytest.fixture
    def augmentor(self) -> MemoryAugmentor:
        return NullExternalMemoryProvider()

    def test_declines_ingestion_explicitly(self) -> None:
        from never4ga.domain.identity import ConceptId
        from never4ga.domain.memory import MemoryEpisode

        receipt = NullExternalMemoryProvider().ingest_episode(
            MemoryEpisode(workspace_id=ConceptId.new(), kind="checkpoint", text="x")
        )
        assert receipt.accepted is False
        assert receipt.detail


class TestNullTemporalGraphProvider(TemporalGraphProviderContract):
    @pytest.fixture
    def provider(self) -> TemporalGraphProvider:
        return NullTemporalGraphProvider()


MEMORY_CANDIDATES = (
    MemoryCandidate(
        provider="fake-memory",
        external_id=ExternalId(provider="fake-memory", value="mem-1"),
        text="Never4gA prefers mechanical context acquisition.",
        provider_confidence=0.8,
    ),
    MemoryCandidate(
        provider="fake-memory",
        external_id=ExternalId(provider="fake-memory", value="mem-2"),
        text="OpenProject remains authoritative for tickets.",
    ),
)


class TestFakeMemoryAugmentor(MemoryAugmentorContract):
    """The same contract, against a provider that actually returns candidates."""

    @pytest.fixture
    def augmentor(self) -> MemoryAugmentor:
        return FakeMemoryAugmentor(MEMORY_CANDIDATES)

    def test_search_actually_returns_candidates(self, augmentor: MemoryAugmentor) -> None:
        assert augmentor.search(MemoryQuery(terms=("mechanical",)))

    def test_returned_candidates_are_never_canonical(self, augmentor: MemoryAugmentor) -> None:
        results = augmentor.search(MemoryQuery())
        assert results
        for candidate in results:
            assert candidate.concept_id is None
            assert isinstance(candidate.external_id, ExternalId)

    def test_ingestion_returns_a_provider_side_identity(self, augmentor: MemoryAugmentor) -> None:
        receipt = augmentor.ingest_episode(
            MemoryEpisode(workspace_id=ConceptId.new(), kind="checkpoint", text="x")
        )
        assert receipt.accepted is True
        assert receipt.external_id is not None
        assert receipt.external_id.provider == augmentor.provider_id
