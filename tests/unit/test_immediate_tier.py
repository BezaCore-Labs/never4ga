"""Validating what just changed, without reading the whole vault.

`details/data-indexing-maintenance.md` §22 puts schema validation, link and
relation validation and duplicate IDs on the **immediate** tier, run on every
change. A watchdog event fires on every save, so that tier cannot afford a
whole-vault diagnosis.

It does not need one. Schema v0.1 validation is relational: it needs every
concept id to check a relation target, and the domain vocabulary to check a
domain. Both are known without walking the corpus. Every id comes from an empty
`MetadataQuery` against the index, which matches everything, and the domain
registry is one read at its reserved path.

Validating one document against a stale set of ids would invent broken
relations, so the immediate tier runs after the reconcile that indexed the
change, never before.

Resolution is scoped to the paths that were looked at. The ledger's ordinary
`reconcile` resolves by absence, which is only true of a complete diagnosis. A
run that looked at three documents may close a finding about those three and
must not touch any other.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from never4ga.adapters.fakes import InMemoryMaintenanceFindings
from never4ga.adapters.fakes.document_store import InMemoryDocumentStore
from never4ga.adapters.fakes.metadata_index import InMemoryMetadataIndex
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.schema import ValidationLevel
from never4ga.services.maintenance import MaintenanceLedger, validate_changed

FIRST = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 30, 13, 0, tzinfo=UTC)

ONE = ConceptId.parse("01a04bd1-7357-7002-8bbc-8e82bcb91959")
TWO = ConceptId.parse("01a04bd1-7357-7002-8bbc-8e82bcb9195a")
ABSENT = ConceptId.parse("01a04bd1-7357-7002-8bbc-8e82bcb9195f")


def a_note(concept_id: ConceptId, name: str, **frontmatter: object) -> StoredDocument:
    return StoredDocument(
        concept_id=concept_id,
        path=VaultPath.parse(f"30_Knowledge/Notes/{name}.md"),
        frontmatter={
            "type": "knowledge",
            "id": str(concept_id),
            "schema": "never4ga/0.1",
            "title": name.title(),
            "created_at": "2026-08-23T12:00:00Z",
            **frontmatter,
        },
        body="body",
    )


@pytest.fixture
def documents() -> InMemoryDocumentStore:
    store = InMemoryDocumentStore()
    store.put(a_note(ONE, "one"))
    store.put(a_note(TWO, "two"))
    return store


@pytest.fixture
def metadata(documents: InMemoryDocumentStore) -> InMemoryMetadataIndex:
    index = InMemoryMetadataIndex()
    for document in documents.iter_documents():
        index.upsert(
            MetadataRecord(
                concept_id=document.concept_id,
                concept_type="knowledge",
                path=document.path,
                title=str(document.frontmatter["title"]),
            )
        )
    return index


def check(
    documents: InMemoryDocumentStore,
    metadata: InMemoryMetadataIndex,
    *paths: str,
) -> list[object]:
    return list(
        validate_changed(
            documents,
            metadata,
            [VaultPath.parse(p) for p in paths],
            level=ValidationLevel.STRICT,
        )
    )


class TestWhatItLooksAt:
    def test_a_clean_document_produces_nothing(
        self, documents: InMemoryDocumentStore, metadata: InMemoryMetadataIndex
    ) -> None:
        assert check(documents, metadata, "30_Knowledge/Notes/one.md") == []

    def test_an_invalid_document_is_reported(
        self, documents: InMemoryDocumentStore, metadata: InMemoryMetadataIndex
    ) -> None:
        documents.put(a_note(ONE, "one", created_at="not a timestamp"))
        (finding,) = check(documents, metadata, "30_Knowledge/Notes/one.md")
        assert finding.path == VaultPath.parse("30_Knowledge/Notes/one.md")  # type: ignore[attr-defined]
        assert finding.document_id == ONE  # type: ignore[attr-defined]

    def test_a_document_it_was_not_asked_about_is_not_reported(
        self, documents: InMemoryDocumentStore, metadata: InMemoryMetadataIndex
    ) -> None:
        documents.put(a_note(TWO, "two", created_at="not a timestamp"))
        assert check(documents, metadata, "30_Knowledge/Notes/one.md") == []

    def test_a_deleted_document_is_not_an_error(
        self, documents: InMemoryDocumentStore, metadata: InMemoryMetadataIndex
    ) -> None:
        # A batch names what changed, and a delete is a change. There is
        # nothing to validate and nothing wrong.
        assert check(documents, metadata, "30_Knowledge/Notes/gone.md") == []


class TestTheIndexSuppliesWhatTheCorpusUsedTo:
    def test_a_relation_to_an_indexed_document_is_fine(
        self, documents: InMemoryDocumentStore, metadata: InMemoryMetadataIndex
    ) -> None:
        # TWO is known because the index knows it, not because the corpus was
        # walked to find out.
        documents.put(a_note(ONE, "one", relations=[{"type": "superseded_by", "target": str(TWO)}]))
        assert check(documents, metadata, "30_Knowledge/Notes/one.md") == []

    def test_a_relation_to_nothing_is_reported(
        self, documents: InMemoryDocumentStore, metadata: InMemoryMetadataIndex
    ) -> None:
        documents.put(
            a_note(ONE, "one", relations=[{"type": "superseded_by", "target": str(ABSENT)}])
        )
        assert check(documents, metadata, "30_Knowledge/Notes/one.md") != []


class TestScopedResolution:
    @pytest.fixture
    def store(self) -> InMemoryMaintenanceFindings:
        return InMemoryMaintenanceFindings()

    def test_a_finding_about_a_changed_document_is_opened(
        self,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
        store: InMemoryMaintenanceFindings,
    ) -> None:
        documents.put(a_note(ONE, "one", created_at="not a timestamp"))
        found = check(documents, metadata, "30_Knowledge/Notes/one.md")
        ledger = MaintenanceLedger(store, now=lambda: FIRST)
        ledger.reconcile_within(found, {VaultPath.parse("30_Knowledge/Notes/one.md")})  # type: ignore[arg-type]
        assert len(store.open_findings()) == 1

    def test_fixing_it_closes_it(
        self,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
        store: InMemoryMaintenanceFindings,
    ) -> None:
        one = VaultPath.parse("30_Knowledge/Notes/one.md")
        documents.put(a_note(ONE, "one", created_at="not a timestamp"))
        MaintenanceLedger(store, now=lambda: FIRST).reconcile_within(
            check(documents, metadata, "30_Knowledge/Notes/one.md"),  # type: ignore[arg-type]
            {one},
        )
        documents.put(a_note(ONE, "one"))
        MaintenanceLedger(store, now=lambda: LATER).reconcile_within(
            check(documents, metadata, "30_Knowledge/Notes/one.md"),  # type: ignore[arg-type]
            {one},
        )
        assert list(store.open_findings()) == []

    def test_a_finding_about_another_document_is_left_alone(
        self,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
        store: InMemoryMaintenanceFindings,
    ) -> None:
        """A scoped run resolves only within the paths it looked at.

        Resolving by absence across the rest of the vault would close every
        other finding and record "fixed" for problems nobody looked at.
        """
        one = VaultPath.parse("30_Knowledge/Notes/one.md")
        two = VaultPath.parse("30_Knowledge/Notes/two.md")
        documents.put(a_note(ONE, "one", created_at="not a timestamp"))
        documents.put(a_note(TWO, "two", created_at="not a timestamp"))
        both = check(documents, metadata, "30_Knowledge/Notes/one.md", "30_Knowledge/Notes/two.md")
        MaintenanceLedger(store, now=lambda: FIRST).reconcile_within(both, {one, two})  # type: ignore[arg-type]
        assert len(store.open_findings()) == 2

        documents.put(a_note(ONE, "one"))
        MaintenanceLedger(store, now=lambda: LATER).reconcile_within(
            check(documents, metadata, "30_Knowledge/Notes/one.md"),  # type: ignore[arg-type]
            {one},
        )
        (remaining,) = store.open_findings()
        assert remaining.path == two

    def test_a_finding_with_no_path_is_never_in_scope(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        """`index_is_stale` belongs to the vault, not to a document.

        Nothing pathless can be resolved by a run that looked at three files,
        because no set of paths contains it.
        """
        from never4ga.schema import Severity
        from never4ga.services.doctor import Finding

        pathless = Finding("index_is_stale", "the index is behind", Severity.WARNING)
        MaintenanceLedger(store, now=lambda: FIRST).reconcile_within([pathless], set())
        assert list(store.open_findings()) == []
