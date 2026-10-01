"""Search and context carry a foreign note (core/02 section 3.3).

A note in a directory registered as foreign material is indexed by path. It
comes back as a search result or a Context Pack item with a path, an excerpt
and the reason `foreign_material`, and no `id`, because a foreign note has none
and nothing here invents one.

Run against the fakes, as `test_foreign_material_indexing.py` is, so the
pipeline is held to its ports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

import pytest

from never4ga.adapters.fakes import (
    InMemoryDocumentStore,
    InMemoryGraphIndex,
    InMemoryIndexState,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.context.focused import FocusedContextAssembler
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.context import ContextBudget, ContextDepth, ContextPack, ContextRequest
from never4ga.domain.document import StoredDocument, UntrackedMarkdown, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionStage, ReasonCode
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.layout import FOREIGN_MATERIAL_FIELD, SYSTEM_MANIFEST
from never4ga.services.indexing import IndexService
from never4ga.services.search import SearchRequest, SearchService, foreign_note_at

RAISED_BEDS = VaultPath.parse("Projects/garden/raised-beds.md")
FAST_AND_SLOW = VaultPath.parse("Reading/fast-and-slow.md")
UNREGISTERED = VaultPath.parse("Elsewhere/stray.md")

WORKSPACE = ConceptId.new()
WORKSPACE_PATH = VaultPath.parse("10_Workspaces/Garden/workspace.md")
OTHER_WORKSPACE = ConceptId.new()
MANIFEST = ConceptId.new()
REPO = PurePosixPath("/home/someone/Projects/garden")


def frozen_clock() -> datetime:
    return datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def concept(
    store: InMemoryDocumentStore,
    path: VaultPath,
    body: str,
    *,
    concept_id: ConceptId | None = None,
    **frontmatter: object,
) -> ConceptId:
    identity = concept_id or ConceptId.new()
    store.put(
        StoredDocument(
            concept_id=identity,
            path=path,
            frontmatter={
                "type": "knowledge",
                "id": str(identity),
                "schema": "never4ga/0.1",
                "title": path.name.removesuffix(".md"),
                "created_at": "2026-09-19T12:00:00Z",
                **frontmatter,
            },
            body=body,
        )
    )
    return identity


def place(store: InMemoryDocumentStore, path: VaultPath, body: str, **frontmatter: object) -> None:
    store.place_untracked(UntrackedMarkdown(path=path, frontmatter=frontmatter, body=body))


@pytest.fixture
def documents() -> InMemoryDocumentStore:
    store = InMemoryDocumentStore()
    concept(
        store,
        SYSTEM_MANIFEST,
        "# Vault\n",
        concept_id=MANIFEST,
        type="system_manifest",
        **{FOREIGN_MATERIAL_FIELD: ["Projects", "Reading"]},
    )
    concept(
        store,
        WORKSPACE_PATH,
        "# Garden\n\nThe garden workspace.\n",
        concept_id=WORKSPACE,
        type="workspace",
        title="Garden",
    )
    concept(
        store,
        VaultPath.parse("10_Workspaces/Other/workspace.md"),
        "# Other\n",
        concept_id=OTHER_WORKSPACE,
        type="workspace",
        title="Other",
    )
    place(
        store,
        RAISED_BEDS,
        "# Raised beds\n\nCedar, not pine, for the compost bays.\n",
    )
    place(
        store,
        FAST_AND_SLOW,
        "# Fast and slow\n\nSystem one is quick.\n",
        title="Thinking, Fast and Slow",
        tags=["books"],
    )
    place(store, UNREGISTERED, "# Stray\n\nCedar again, but nobody registered this.\n")
    return store


@pytest.fixture
def metadata() -> InMemoryMetadataIndex:
    return InMemoryMetadataIndex()


@pytest.fixture
def text() -> InMemoryTextIndex:
    return InMemoryTextIndex()


@pytest.fixture
def graph() -> InMemoryGraphIndex:
    return InMemoryGraphIndex()


@pytest.fixture
def indexer(
    documents: InMemoryDocumentStore,
    metadata: InMemoryMetadataIndex,
    text: InMemoryTextIndex,
    graph: InMemoryGraphIndex,
) -> IndexService:
    service = IndexService(
        documents=documents,
        metadata=metadata,
        text=text,
        graph=graph,
        state=InMemoryIndexState(),
        clock=frozen_clock,
    )
    service.reconcile()
    return service


@pytest.fixture
def search(
    documents: InMemoryDocumentStore,
    metadata: InMemoryMetadataIndex,
    text: InMemoryTextIndex,
    graph: InMemoryGraphIndex,
    indexer: IndexService,
) -> SearchService:
    return SearchService(
        documents=documents,
        metadata=metadata,
        text=text,
        graph=graph,
        index=indexer,
        clock=frozen_clock,
    )


@pytest.fixture
def assembler(
    documents: InMemoryDocumentStore,
    metadata: InMemoryMetadataIndex,
    text: InMemoryTextIndex,
    graph: InMemoryGraphIndex,
    indexer: IndexService,
) -> FocusedContextAssembler:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=WORKSPACE, workspace_path=WORKSPACE_PATH, repository_root=REPO
        )
    )
    return FocusedContextAssembler(
        resolver=MechanicalScopeResolver(registry),
        metadata_index=metadata,
        text_index=text,
        graph_index=graph,
        document_store=documents,
    )


def focus(assembler: FocusedContextAssembler, *terms: str) -> ContextPack:
    return assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO),
            depth=ContextDepth.FOCUSED,
            budget=ContextBudget(max_items=20, max_characters=50_000),
            terms=terms,
        )
    )


class TestSearch:
    def test_a_foreign_note_is_a_result_with_a_path_and_no_id(self, search: SearchService) -> None:
        (result,) = search.search(SearchRequest(query="compost")).results
        assert result.concept_id is None
        assert result.path == RAISED_BEDS
        assert result.concept_type is None

    def test_its_reason_is_foreign_material(self, search: SearchService) -> None:
        (result,) = search.search(SearchRequest(query="compost")).results
        assert result.reason.code == ReasonCode.FOREIGN_MATERIAL
        assert result.reason.stage == AcquisitionStage.LEXICAL

    def test_its_excerpt_and_lines_come_from_the_note(self, search: SearchService) -> None:
        (result,) = search.search(SearchRequest(query="compost")).results
        assert "Cedar, not pine" in result.excerpt
        assert result.line_range is not None
        assert result.heading_path == ("Raised beds",)
        assert not result.chunk_has_changed

    def test_it_is_titled_by_what_the_writer_wrote(self, search: SearchService) -> None:
        (heading,) = search.search(SearchRequest(query="compost")).results
        assert heading.title == "Raised beds"
        (declared,) = search.search(SearchRequest(query="quick")).results
        assert declared.title == "Thinking, Fast and Slow"

    def test_it_carries_nothing_only_a_concept_has(self, search: SearchService) -> None:
        (result,) = search.search(SearchRequest(query="compost")).results
        assert result.workspace_id is None
        assert result.authority is None
        assert result.status is None
        assert result.lifecycle is None

    def test_it_ranks_among_concepts(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        concept(
            documents,
            VaultPath.parse("30_Knowledge/Notes/compost.md"),
            "# Compost\n\nCompost compost compost.\n",
        )
        indexer.reconcile()
        results = search.search(SearchRequest(query="compost")).results
        assert [str(result.path) for result in results] == [
            "30_Knowledge/Notes/compost.md",
            str(RAISED_BEDS),
        ]
        assert [result.rank for result in results] == [1, 2]

    def test_a_structural_filter_leaves_the_pile_out(self, search: SearchService) -> None:
        # A foreign note has no type, tag, domain or workspace, so no filter
        # on one can admit it.
        response = search.search(SearchRequest(query="compost", types=("knowledge",)))
        assert response.results == ()

    def test_an_unregistered_directory_is_not_searched(self, search: SearchService) -> None:
        results = search.search(SearchRequest(query="cedar")).results
        assert [str(result.path) for result in results] == [str(RAISED_BEDS)]

    def test_a_note_edited_since_indexing_says_its_passage_moved(
        self, search: SearchService, documents: InMemoryDocumentStore
    ) -> None:
        place(documents, RAISED_BEDS, "# Raised beds\n\nRewritten entirely.\n")
        (result,) = search.search(SearchRequest(query="compost")).results
        assert result.chunk_has_changed
        assert "Rewritten entirely" in result.excerpt

    def test_a_note_deleted_since_indexing_still_answers_by_path(
        self, search: SearchService, documents: InMemoryDocumentStore
    ) -> None:
        documents.remove_untracked(RAISED_BEDS)
        (result,) = search.search(SearchRequest(query="compost")).results
        assert result.path == RAISED_BEDS
        assert result.excerpt == ""
        assert result.chunk_has_changed


class TestFocusedContext:
    def test_a_foreign_note_is_a_retrieved_item_with_no_id(
        self, assembler: FocusedContextAssembler
    ) -> None:
        pack = focus(assembler, "compost")
        (item,) = [item for item in pack.items if item.path == RAISED_BEDS]
        assert item.concept_id is None
        assert item.category == "retrieved"
        assert item.reason.code == ReasonCode.FOREIGN_MATERIAL
        assert item.title == "Raised beds"
        assert item.body is not None and "Cedar, not pine" in item.body

    def test_the_pile_is_reached_from_any_workspace(
        self, assembler: FocusedContextAssembler
    ) -> None:
        # A pile belongs to no workspace, so the Stage B reduction admits it
        # beside the workspace's own concepts rather than shutting it out.
        pack = focus(assembler, "quick")
        assert [item.path for item in pack.items] == [FAST_AND_SLOW]

    def test_the_pile_ranks_beside_the_workspaces_own_concepts(
        self,
        assembler: FocusedContextAssembler,
        documents: InMemoryDocumentStore,
        indexer: IndexService,
    ) -> None:
        own = concept(
            documents,
            VaultPath.parse("10_Workspaces/Garden/Context/compost.md"),
            "# Compost\n\nCompost compost compost.\n",
            type="context",
            workspace=str(WORKSPACE),
        )
        indexer.reconcile()
        pack = focus(assembler, "compost")
        assert [item.concept_id for item in pack.items] == [own, None]
        assert pack.items[1].path == RAISED_BEDS

    def test_another_workspaces_concept_is_still_out_of_scope(
        self,
        assembler: FocusedContextAssembler,
        documents: InMemoryDocumentStore,
        indexer: IndexService,
    ) -> None:
        concept(
            documents,
            VaultPath.parse("10_Workspaces/Other/Context/compost.md"),
            "# Compost elsewhere\n\nCompost.\n",
            type="context",
            workspace=str(OTHER_WORKSPACE),
        )
        indexer.reconcile()
        assert [item.path for item in focus(assembler, "compost").items] == [RAISED_BEDS]


class TestNotAConcept:
    def test_a_foreign_note_is_found_by_its_path(self, documents: InMemoryDocumentStore) -> None:
        note = foreign_note_at(documents, str(RAISED_BEDS))
        assert note is not None
        assert note.path == RAISED_BEDS

    def test_an_identity_is_not_a_path(self, documents: InMemoryDocumentStore) -> None:
        assert foreign_note_at(documents, str(WORKSPACE)) is None

    def test_a_concept_is_not_a_foreign_note(self, documents: InMemoryDocumentStore) -> None:
        assert foreign_note_at(documents, str(WORKSPACE_PATH)) is None

    def test_an_unregistered_directory_holds_no_foreign_note(
        self, documents: InMemoryDocumentStore
    ) -> None:
        assert foreign_note_at(documents, str(UNREGISTERED)) is None

    def test_nonsense_is_not_an_error(self, documents: InMemoryDocumentStore) -> None:
        assert foreign_note_at(documents, "../outside.md") is None
        assert foreign_note_at(documents, "") is None
