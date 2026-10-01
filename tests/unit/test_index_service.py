"""The indexing pipeline, run entirely against fakes.

core/06 section 25 test A: higher-level code must run against any backend. This
suite never touches SQLite, which is how the pipeline stays written against
ports rather than against a database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from never4ga.adapters.fakes import (
    InMemoryDocumentStore,
    InMemoryGraphIndex,
    InMemoryIndexState,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.graph_index import Direction
from never4ga.ports.metadata_index import MetadataQuery
from never4ga.ports.text_index import TextQuery
from never4ga.services.indexing import IndexService


def frozen_clock() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def documents() -> InMemoryDocumentStore:
    return InMemoryDocumentStore()


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


def make(
    store: InMemoryDocumentStore,
    *,
    path: str = "30_Knowledge/Notes/thing.md",
    body: str = "# One\nbody\n",
    concept_id: ConceptId | None = None,
    **frontmatter: Any,
) -> StoredDocument:
    concept_id = concept_id or ConceptId.new()
    base: dict[str, Any] = {
        "type": "knowledge",
        "id": str(concept_id),
        "schema": "never4ga/0.1",
        "title": "Thing",
        "created_at": "2026-08-23T12:00:00Z",
    }
    base.update(frontmatter)
    document = StoredDocument(
        concept_id=concept_id, path=VaultPath.parse(path), frontmatter=base, body=body
    )
    store.put(document)
    return document


class TestFullReconciliation:
    def test_documents_become_searchable(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        document = make(documents, body="# One\nmechanical context acquisition\n")
        run = service.reconcile()
        assert run.indexed == 1
        found = text.search(TextQuery(terms=("mechanical",)))
        assert [candidate.concept_id for candidate in found] == [document.concept_id]

    def test_metadata_is_projected(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
    ) -> None:
        make(documents, tags=["architecture"])
        service.reconcile()
        assert len(metadata.query(MetadataQuery(tags=("architecture",)))) == 1

    def test_a_deleted_file_disappears_from_the_index(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
        text: InMemoryTextIndex,
    ) -> None:
        document = make(documents, body="# One\nmechanical\n")
        service.reconcile()
        documents.delete(document.concept_id)
        run = service.reconcile()
        assert run.removed == 1
        assert metadata.get(document.concept_id) is None
        assert text.search(TextQuery(terms=("mechanical",))) == ()

    def test_a_move_keeps_identity(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
        state: InMemoryIndexState,
    ) -> None:
        # details/data-indexing-maintenance.md section 14: same UUID, new path,
        # same concept -- never a second identity.
        document = make(documents)
        service.reconcile()
        documents.put(
            StoredDocument(
                concept_id=document.concept_id,
                path=VaultPath.parse("90_Archive/Knowledge/Notes/thing.md"),
                frontmatter=document.frontmatter,
                body=document.body,
            )
        )
        run = service.reconcile()
        assert run.removed == 0
        assert len(state.indexed_documents()) == 1
        record = metadata.get(document.concept_id)
        assert record is not None
        assert str(record.path) == "90_Archive/Knowledge/Notes/thing.md"


class TestIncrementalReconciliation:
    def test_an_unchanged_document_is_skipped(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents)
        service.reconcile()
        run = service.reconcile(changed_only=True)
        assert (run.indexed, run.unchanged) == (0, 1)

    def test_an_edited_document_is_reindexed(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        document = make(documents, body="# One\nobsolete wording\n")
        service.reconcile()
        documents.put(
            StoredDocument(
                concept_id=document.concept_id,
                path=document.path,
                frontmatter=document.frontmatter,
                body="# One\ncurrent wording\n",
            )
        )
        run = service.reconcile(changed_only=True)
        assert run.indexed == 1
        assert text.search(TextQuery(terms=("obsolete",))) == ()
        assert text.search(TextQuery(terms=("current",)))

    def test_an_incremental_pass_still_notices_a_deletion(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        document = make(documents)
        service.reconcile()
        documents.delete(document.concept_id)
        assert service.reconcile(changed_only=True).removed == 1

    def test_a_link_target_that_arrives_later_resolves(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        # On a store with no stats, an unchanged referrer must still be
        # re-resolved, or its link stays broken after the target arrives.
        make(documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[latecomer]].\n")
        service.reconcile()
        assert len(service.health().broken_links) == 1

        make(documents, path="30_Knowledge/Notes/latecomer.md")
        run = service.reconcile(changed_only=True)

        assert service.health().broken_links == ()
        assert (run.indexed, run.unchanged) == (2, 0)

    def test_a_link_target_that_leaves_breaks_the_link(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents, path="30_Knowledge/Notes/referrer.md", body="# R\nSee [[doomed]].\n")
        doomed = make(documents, path="30_Knowledge/Notes/doomed.md")
        service.reconcile()

        documents.delete(doomed.concept_id)
        service.reconcile(changed_only=True)

        assert [link.target for link in service.health().broken_links] == ["doomed"]


class TestRebuild:
    def test_rebuild_is_deterministic(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        make(documents, body="# One\nmechanical\n\n## Two\ncontext\n")

        def keys() -> list[str]:
            found = text.search(TextQuery(terms=("mechanical", "context")))
            return [c.chunk.key for c in found if c.chunk is not None]

        service.rebuild()
        first = keys()
        service.rebuild()
        assert keys() == first
        assert first

    def test_discarding_every_projection_and_rebuilding_recovers_it(
        self,
        service: IndexService,
        documents: InMemoryDocumentStore,
        metadata: InMemoryMetadataIndex,
        text: InMemoryTextIndex,
        graph: InMemoryGraphIndex,
        state: InMemoryIndexState,
    ) -> None:
        # details/data-indexing-maintenance.md section 17.
        target = make(documents, path="30_Knowledge/Notes/other.md")
        source = make(
            documents,
            body="# One\nmechanical, see [other](other.md)\n",
            relations=[{"type": "depends_on", "target": str(target.concept_id)}],
        )
        service.reconcile()
        before = (
            len(metadata.query(MetadataQuery())),
            len(text.search(TextQuery(terms=("mechanical",)))),
            len(graph.neighbors(source.concept_id, direction=Direction.OUTGOING)),
            len(state.links()),
        )

        metadata.clear()
        text.clear()
        graph.clear()
        state.clear()
        service.rebuild()

        assert (
            len(metadata.query(MetadataQuery())),
            len(text.search(TextQuery(terms=("mechanical",)))),
            len(graph.neighbors(source.concept_id, direction=Direction.OUTGOING)),
            len(state.links()),
        ) == before


class TestRelations:
    def test_typed_relations_are_projected(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other.md")
        source = make(
            documents, relations=[{"type": "depends_on", "target": str(target.concept_id)}]
        )
        service.reconcile()
        neighbors = graph.neighbors(source.concept_id, direction=Direction.OUTGOING)
        assert [n.concept_id for n in neighbors] == [target.concept_id]

    def test_reindexing_one_document_keeps_relations_pointing_at_it(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other.md")
        make(documents, relations=[{"type": "depends_on", "target": str(target.concept_id)}])
        service.reconcile()
        documents.put(
            StoredDocument(
                concept_id=target.concept_id,
                path=target.path,
                frontmatter=target.frontmatter,
                body="# Edited\nnew prose\n",
            )
        )
        service.reconcile(changed_only=True)
        assert len(graph.neighbors(target.concept_id, direction=Direction.INCOMING)) == 1


class TestLinks:
    def test_a_resolved_link_records_its_target(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other.md")
        make(documents, body="see [other](other.md)\n")
        service.reconcile()
        (link,) = state.links()
        assert link.target_id == target.concept_id
        assert not link.is_broken

    def test_a_link_to_nothing_is_recorded_as_broken(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        make(documents, body="see [missing](missing.md)\n")
        service.reconcile()
        assert [link.is_broken for link in state.links()] == [True]

    def test_an_external_link_is_not_recorded(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        make(documents, body="see [spec](https://example.invalid/x)\n")
        service.reconcile()
        assert state.links() == ()


class TestWikilinkResolution:
    """Exactly what Obsidian matches, minus its heuristics.

    An exact filename stem, then aliases, case-insensitively. Not the title,
    because Obsidian does not match titles either and a link that fails in the
    editor should fail here. Two documents answering to one name is a finding,
    not a guess.
    """

    def test_a_bare_wikilink_resolves_by_filename(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other-note.md")
        make(documents, body="see [[other-note]]\n")
        service.reconcile()
        (link,) = state.links()
        assert link.target_id == target.concept_id

    def test_matching_is_case_insensitive(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other-note.md")
        make(documents, body="see [[Other-Note]]\n")
        service.reconcile()
        assert state.links()[0].target_id == target.concept_id

    def test_an_alias_resolves(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other-note.md", aliases=["The Other One"])
        make(documents, body="see [[The Other One]]\n")
        service.reconcile()
        assert state.links()[0].target_id == target.concept_id

    def test_a_title_does_not_resolve(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        # Obsidian would not resolve this either. Agreeing with the editor is
        # the point: a link that is broken there is broken here.
        make(documents, path="30_Knowledge/Notes/other-note.md", title="Something Else")
        make(documents, body="see [[Something Else]]\n")
        service.reconcile()
        assert state.links()[0].is_broken

    def test_a_name_matching_nothing_is_broken(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        make(documents, body="see [[nowhere]]\n")
        service.reconcile()
        (link,) = state.links()
        assert link.is_broken
        assert link.target_name == "nowhere"

    def test_an_ambiguous_name_is_a_finding_not_a_guess(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        make(documents, path="10_Workspaces/a/notes.md")
        make(documents, path="30_Knowledge/Notes/notes.md")
        make(documents, path="30_Knowledge/Notes/source.md", body="see [[notes]]\n")
        run = service.reconcile()
        assert "ambiguous_link" in {issue.code for issue in run.issues}
        (link,) = [link for link in state.links() if link.target_name == "notes"]
        assert link.is_broken

    def test_a_document_naming_itself_twice_is_still_one_document(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        """`aliases` repeating the stem must not make the document unreachable.

        A record whose stem and alias are the same name is the obvious thing to
        write. It must count as one match, not be reported as `ambiguous_link:
        matches 2 documents` so that every `[[ollama]]` resolves to nothing.
        """
        target = make(documents, path="40_Entities/Records/ollama.md", aliases=["ollama", "Ollama"])
        make(documents, path="30_Knowledge/Notes/source.md", body="see [[Ollama]]\n")
        run = service.reconcile()
        assert "ambiguous_link" not in {issue.code for issue in run.issues}
        (link,) = [link for link in state.links() if link.target_name == "Ollama"]
        assert link.target_id == target.concept_id
        assert not link.is_broken

    def test_a_path_style_wikilink_resolves_like_a_path(
        self, service: IndexService, documents: InMemoryDocumentStore, state: InMemoryIndexState
    ) -> None:
        target = make(documents, path="30_Knowledge/Maps/m.md")
        make(documents, body="see [[30_Knowledge/Maps/m]]\n")
        service.reconcile()
        (link,) = state.links()
        assert link.target_id == target.concept_id
        assert link.target_path is not None

    def test_health_counts_a_broken_wikilink(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents, body="see [[nowhere]]\n")
        service.reconcile()
        assert len(service.health().broken_links) == 1


class TestReporting:
    def test_projection_issues_reach_the_run(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents, relations="depends_on")
        run = service.reconcile()
        assert "invalid_relations" in {issue.code for issue in run.issues}

    def test_a_document_with_problems_is_still_indexed(
        self, service: IndexService, documents: InMemoryDocumentStore, text: InMemoryTextIndex
    ) -> None:
        # details/data-indexing-maintenance.md section 12: "Where safe, useful
        # text may still be indexed with a validation warning."
        make(documents, body="# One\nmechanical\n", relations="depends_on")
        run = service.reconcile()
        assert run.indexed == 1
        assert text.search(TextQuery(terms=("mechanical",)))


class TestHealth:
    def test_a_fresh_index_is_current(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents)
        service.reconcile()
        health = service.health()
        assert not health.is_stale
        assert health.indexed_documents == 1

    def test_an_unindexed_document_makes_the_index_stale(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents)
        health = service.health()
        assert health.is_stale
        assert len(health.new) == 1

    def test_an_edited_document_makes_the_index_stale(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        document = make(documents)
        service.reconcile()
        documents.put(
            StoredDocument(
                concept_id=document.concept_id,
                path=document.path,
                frontmatter=document.frontmatter,
                body="# One\nedited\n",
            )
        )
        health = service.health()
        assert [str(path) for path in health.changed] == [str(document.path)]

    def test_a_removed_document_makes_the_index_stale(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        document = make(documents)
        service.reconcile()
        documents.delete(document.concept_id)
        assert len(service.health().missing) == 1

    def test_health_reports_broken_links(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents, body="see [missing](missing.md)\n")
        service.reconcile()
        assert len(service.health().broken_links) == 1

    def test_health_reports_a_relation_to_nothing(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        make(documents, relations=[{"type": "depends_on", "target": str(ConceptId.new())}])
        service.reconcile()
        (unresolved,) = service.health().unresolved_relations
        assert unresolved.relation_type == "depends_on"

    def test_reading_never_repairs(
        self, service: IndexService, documents: InMemoryDocumentStore
    ) -> None:
        # Reads report a stale index rather than silently reindexing it.
        make(documents)
        assert service.health().is_stale
        assert service.health().is_stale
        assert service.health().indexed_documents == 0


class TestGraphSourcesBeyondTypedRelations:
    """core/06 section 12 names more than one canonical graph source.

    "Canonical graph sources already exist: typed relations in YAML, Markdown
    links, workspace parent/child, ... SQLite v0.1 projects these into edge
    tables." Markdown links and workspace parents are projected as edges too,
    not only used for link integrity. Typed relations alone leave the
    relational lane almost nothing to traverse, and focused retrieval becomes
    single-lane without saying so.
    """

    def test_a_resolved_markdown_link_becomes_an_edge(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other.md")
        source = make(documents, body="see [other](other.md)\n")
        service.reconcile()
        neighbors = graph.neighbors(source.concept_id, direction=Direction.OUTGOING)
        assert [n.concept_id for n in neighbors] == [target.concept_id]
        assert [n.relation_type for n in neighbors] == ["links_to"]

    def test_a_bare_wikilink_resolved_by_name_becomes_an_edge(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other.md")
        source = make(documents, body="see [[other]]\n")
        service.reconcile()
        neighbors = graph.neighbors(source.concept_id, direction=Direction.OUTGOING)
        assert [n.concept_id for n in neighbors] == [target.concept_id]

    def test_a_link_that_resolves_to_nothing_is_not_an_edge(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        """A broken link is a `doctor` finding, not a traversable relationship."""
        source = make(documents, body="see [gone](gone.md)\n")
        service.reconcile()
        assert graph.neighbors(source.concept_id, direction=Direction.OUTGOING) == ()

    def test_an_external_link_is_not_an_edge(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        source = make(documents, body="see [docs](https://example.invalid/page)\n")
        service.reconcile()
        assert graph.neighbors(source.concept_id, direction=Direction.OUTGOING) == ()

    def test_a_workspace_parent_becomes_an_edge(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        parent = make(documents, path="10_Workspaces/Parent/workspace.md", type="workspace")
        child = make(
            documents,
            path="10_Workspaces/Parent/Workspaces/Child/workspace.md",
            type="workspace",
            parent=str(parent.concept_id),
        )
        service.reconcile()
        neighbors = graph.neighbors(child.concept_id, direction=Direction.OUTGOING)
        assert [n.concept_id for n in neighbors] == [parent.concept_id]
        assert [n.relation_type for n in neighbors] == ["parent"]

    def test_membership_in_a_workspace_is_not_an_edge(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        """Otherwise every document in a workspace is one hop from every other.

        Membership is Stage B's structural reduction and is already how the
        candidate set is bounded. Making it a relationship as well would let the
        graph lane return the whole workspace from any starting point.
        """
        workspace = make(documents, path="10_Workspaces/W/workspace.md", type="workspace")
        member = make(documents, workspace=str(workspace.concept_id))
        service.reconcile()
        assert graph.neighbors(member.concept_id, direction=Direction.OUTGOING) == ()

    def test_a_typed_relation_survives_a_link_to_the_same_document(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        """Both are true and both are kept; the type is what tells them apart."""
        target = make(documents, path="30_Knowledge/Notes/other.md")
        source = make(
            documents,
            body="see [other](other.md)\n",
            relations=[{"type": "depends_on", "target": str(target.concept_id)}],
        )
        service.reconcile()
        neighbors = graph.neighbors(source.concept_id, direction=Direction.OUTGOING)
        assert sorted(n.relation_type for n in neighbors) == ["depends_on", "links_to"]

    def test_a_document_linking_to_itself_is_not_an_edge(
        self, service: IndexService, documents: InMemoryDocumentStore, graph: InMemoryGraphIndex
    ) -> None:
        """A bare anchor addresses the document it sits in, and that is not a relationship.

        It matters more than it looks: once the graph lane corroborates rather
        than only expands, a self-edge would let any document with an in-page
        anchor vote for itself and rise for having a table of contents.
        """
        source = make(documents, body="# One\nsee [the section](#one)\n")
        service.reconcile()
        assert graph.neighbors(source.concept_id, direction=Direction.OUTGOING) == ()
