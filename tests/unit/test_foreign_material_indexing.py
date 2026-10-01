"""Foreign material through the scanner (core/01 section 1).

A note in a directory `init` registered as foreign material is chunked and
indexed by its path, so `search` can reach it before anybody has adopted it.
The path is derived and rebuildable and never an identity: the note appears in
no metadata or graph projection, a rename is a new record and the old one
gone, and a deletion takes its record with it.

Run against the fakes, as `test_index_service.py` is, so the pipeline is held
to its ports (core/06 section 25 test A).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from never4ga.adapters.fakes import (
    InMemoryDocumentStore,
    InMemoryGraphIndex,
    InMemoryIndexState,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.domain.document import StoredDocument, UntrackedMarkdown, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.layout import FOREIGN_MATERIAL_FIELD, SYSTEM_MANIFEST
from never4ga.ports.metadata_index import MetadataQuery
from never4ga.ports.text_index import TextQuery
from never4ga.services.indexing import IndexService

RAISED_BEDS = VaultPath.parse("Projects/garden/raised-beds.md")
FAST_AND_SLOW = VaultPath.parse("Reading/fast-and-slow.md")


def frozen_clock() -> datetime:
    return datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def documents() -> InMemoryDocumentStore:
    store = InMemoryDocumentStore()
    register(store, "Projects", "Reading")
    return store


@pytest.fixture
def text() -> InMemoryTextIndex:
    return InMemoryTextIndex()


@pytest.fixture
def metadata() -> InMemoryMetadataIndex:
    return InMemoryMetadataIndex()


@pytest.fixture
def graph() -> InMemoryGraphIndex:
    return InMemoryGraphIndex()


@pytest.fixture
def state() -> InMemoryIndexState:
    return InMemoryIndexState()


@pytest.fixture
def service(
    documents: InMemoryDocumentStore,
    metadata: InMemoryMetadataIndex,
    text: InMemoryTextIndex,
    graph: InMemoryGraphIndex,
    state: InMemoryIndexState,
) -> IndexService:
    return IndexService(
        documents=documents,
        metadata=metadata,
        text=text,
        graph=graph,
        state=state,
        clock=frozen_clock,
    )


MANIFEST_ID = ConceptId.new()


def register(store: InMemoryDocumentStore, *directories: str) -> None:
    store.put(
        StoredDocument(
            concept_id=MANIFEST_ID,
            path=SYSTEM_MANIFEST,
            frontmatter={
                "type": "system_manifest",
                "id": str(MANIFEST_ID),
                "schema": "never4ga/0.1",
                "title": "Vault",
                "created_at": "2026-09-17T12:00:00Z",
                FOREIGN_MATERIAL_FIELD: list(directories),
            },
            body="# Vault\n",
        )
    )


def place(store: InMemoryDocumentStore, path: VaultPath, body: str, **frontmatter: object) -> None:
    store.place_untracked(UntrackedMarkdown(path=path, frontmatter=frontmatter, body=body))


def found(text: InMemoryTextIndex, *terms: str) -> list[str]:
    return [
        str(candidate.source_path)
        for candidate in text.search(TextQuery(terms=terms))
        if candidate.concept_id is None
    ]


class TestIndexing:
    def test_a_foreign_note_becomes_searchable_by_path(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        place(documents, RAISED_BEDS, "# Raised beds\n\nCedar, not pine.\n")
        run = service.reconcile()
        assert run.foreign_indexed == 1
        assert found(text, "cedar") == [str(RAISED_BEDS)]

    def test_a_note_with_frontmatter_of_its_own_is_indexed_too(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        place(documents, FAST_AND_SLOW, "# Fast and slow\n\nSystem one.\n", tags=["books"])
        service.reconcile()
        assert found(text, "system") == [str(FAST_AND_SLOW)]

    def test_concepts_are_counted_apart_from_foreign_notes(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        place(documents, RAISED_BEDS, "# Raised beds\n")
        place(documents, FAST_AND_SLOW, "# Fast and slow\n")
        run = service.reconcile()
        assert (run.indexed, run.foreign_indexed) == (1, 2)  # the manifest is the one concept

    def test_an_unregistered_directory_is_not_indexed(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        # `doctor` reports it as foreign_material_unregistered; the index
        # never silently takes it in.
        place(documents, VaultPath.parse("Unregistered/note.md"), "Cedar, not pine.\n")
        run = service.reconcile()
        assert run.foreign_indexed == 0
        assert found(text, "cedar") == []

    def test_prose_under_a_root_is_not_foreign_material(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        place(documents, VaultPath.parse("30_Knowledge/Notes/prose.md"), "Cedar, not pine.\n")
        service.reconcile()
        assert found(text, "cedar") == []

    def test_a_vault_with_no_foreign_material_indexes_none(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        register(documents)
        place(documents, RAISED_BEDS, "# Raised beds\n")
        assert service.reconcile().foreign_indexed == 0


class TestNoProjection:
    """Indexed for search only (core/01 section 1)."""

    def test_a_foreign_note_is_in_no_metadata_projection(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
    ) -> None:
        place(documents, RAISED_BEDS, "# Raised beds\n", tags=["garden"])
        service.reconcile()
        assert [str(r.path) for r in metadata.query(MetadataQuery())] == [str(SYSTEM_MANIFEST)]

    def test_nor_in_the_graph(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        place(documents, RAISED_BEDS, "# Raised beds\n\nSee [[Fast and slow]].\n")
        place(documents, FAST_AND_SLOW, "# Fast and slow\n")
        service.reconcile()
        assert graph.neighbors(MANIFEST_ID) == ()

    def test_nor_among_the_concepts_the_index_recorded(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        place(documents, RAISED_BEDS, "# Raised beds\n")
        service.reconcile()
        assert [d.concept_id for d in state.indexed_documents()] == [MANIFEST_ID]
        assert [str(p.path) for p in state.indexed_paths()] == [str(RAISED_BEDS)]

    def test_links_from_a_foreign_note_are_not_recorded(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        # A broken link in somebody's pile is not a finding about the vault.
        place(documents, RAISED_BEDS, "See [missing](nowhere.md).\n")
        service.reconcile()
        assert state.links() == ()


class TestReconciling:
    def test_a_rename_leaves_exactly_one_record_at_the_new_path(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        text: InMemoryTextIndex,
        state: InMemoryIndexState,
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        moved = VaultPath.parse("Projects/garden/beds.md")
        documents.remove_untracked(RAISED_BEDS)
        place(documents, moved, "Cedar, not pine.\n")
        run = service.reconcile()
        assert (run.foreign_indexed, run.foreign_removed) == (1, 1)
        assert found(text, "cedar") == [str(moved)]
        assert [str(p.path) for p in state.indexed_paths()] == [str(moved)]

    def test_a_deletion_removes_the_record(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        text: InMemoryTextIndex,
        state: InMemoryIndexState,
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        documents.remove_untracked(RAISED_BEDS)
        run = service.reconcile()
        assert run.foreign_removed == 1
        assert found(text, "cedar") == []
        assert state.indexed_paths() == ()

    def test_an_edit_replaces_the_chunks(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        place(documents, RAISED_BEDS, "Larch, not cedar.\n")
        service.reconcile(changed_only=True)
        assert found(text, "larch") == [str(RAISED_BEDS)]
        assert found(text, "pine") == []

    def test_an_unchanged_note_is_not_reindexed_by_a_changed_only_pass(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        run = service.reconcile(changed_only=True)
        assert (run.foreign_indexed, run.foreign_unchanged, run.foreign_removed) == (0, 1, 0)

    def test_unregistering_a_directory_drops_its_notes(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        register(documents, "Reading")
        assert service.reconcile().foreign_removed == 1
        assert found(text, "cedar") == []

    def test_a_note_that_gains_an_identity_leaves_the_path_records(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        text: InMemoryTextIndex,
        state: InMemoryIndexState,
    ) -> None:
        # Adoption in place, or a user writing an `id` by hand: the concept
        # replaces the path record on the next pass.
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        concept = ConceptId.new()
        documents.put(
            StoredDocument(
                concept_id=concept,
                path=RAISED_BEDS,
                frontmatter={
                    "type": "knowledge",
                    "id": str(concept),
                    "schema": "never4ga/0.1",
                    "title": "Raised beds",
                    "created_at": "2026-09-17T12:00:00Z",
                },
                body="Cedar, not pine.\n",
            )
        )
        service.reconcile()
        assert state.indexed_paths() == ()
        assert [c.concept_id for c in text.search(TextQuery(terms=("cedar",)))] == [concept]

    def test_rebuild_recovers_foreign_notes(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        run = service.rebuild()
        assert run.foreign_indexed == 1
        assert found(text, "cedar") == [str(RAISED_BEDS)]


class TestHealth:
    def test_an_unindexed_foreign_note_makes_the_index_stale(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        service.reconcile()
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        health = service.health()
        assert health.new == (RAISED_BEDS,)
        assert health.is_stale

    def test_indexing_settles_it(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        health = service.health()
        assert not health.is_stale
        assert health.indexed_foreign == 1

    def test_an_edited_foreign_note_is_changed(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        place(documents, RAISED_BEDS, "Larch.\n")
        assert service.health().changed == (RAISED_BEDS,)

    def test_a_deleted_foreign_note_is_missing_but_names_no_identity(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        place(documents, RAISED_BEDS, "Cedar, not pine.\n")
        service.reconcile()
        documents.remove_untracked(RAISED_BEDS)
        health = service.health()
        assert health.missing == (RAISED_BEDS,)
        assert RAISED_BEDS not in health.missing_identities
