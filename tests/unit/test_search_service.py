"""Composing user-facing results from backend-neutral candidates.

Two contracts stay apart: a retrieval *candidate* (core/06 section 6) is
backend-neutral and carries no excerpt, and a retrieval *result*
(details/retrieval-context-memory.md section 8) is the user-facing shape. This
service turns the first into the second by reading canonical Markdown, so
excerpts never live in the index.
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
from never4ga.domain.provenance import ReasonCode
from never4ga.services.indexing import IndexService
from never4ga.services.search import SearchRequest, SearchService, parse_query


def frozen_clock() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def documents() -> InMemoryDocumentStore:
    return InMemoryDocumentStore()


@pytest.fixture
def backends() -> tuple[
    InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex, InMemoryIndexState
]:
    return (
        InMemoryMetadataIndex(),
        InMemoryTextIndex(),
        InMemoryGraphIndex(),
        InMemoryIndexState(),
    )


@pytest.fixture
def indexer(
    documents: InMemoryDocumentStore,
    backends: tuple[
        InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex, InMemoryIndexState
    ],
) -> IndexService:
    metadata, text, graph, state = backends
    return IndexService(
        documents=documents,
        metadata=metadata,
        text=text,
        graph=graph,
        state=state,
        clock=frozen_clock,
    )


@pytest.fixture
def search(
    documents: InMemoryDocumentStore,
    backends: tuple[
        InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex, InMemoryIndexState
    ],
    indexer: IndexService,
) -> SearchService:
    metadata, text, graph, _state = backends
    return SearchService(
        documents=documents,
        metadata=metadata,
        text=text,
        graph=graph,
        index=indexer,
        clock=frozen_clock,
    )


def make(
    store: InMemoryDocumentStore,
    *,
    path: str = "30_Knowledge/Notes/thing.md",
    body: str = "# Mechanical context\nMechanical acquisition beats guessing.\n",
    **frontmatter: Any,
) -> StoredDocument:
    concept_id = frontmatter.pop("concept_id", None) or ConceptId.new()
    base: dict[str, Any] = {
        "type": "knowledge",
        "id": str(concept_id),
        "schema": "never4ga/0.1",
        "title": "Mechanical context",
        "created_at": "2026-08-23T12:00:00Z",
    }
    base.update(frontmatter)
    document = StoredDocument(
        concept_id=concept_id, path=VaultPath.parse(path), frontmatter=base, body=body
    )
    store.put(document)
    return document


class TestQueryParsing:
    def test_words_become_terms(self) -> None:
        assert parse_query("mechanical context").terms == ("mechanical", "context")

    def test_a_quoted_run_becomes_a_phrase(self) -> None:
        query = parse_query('"mechanical context" acquisition')
        assert query.phrases == ("mechanical context",)
        assert query.terms == ("acquisition",)

    def test_an_identifier_is_recognised(self) -> None:
        assert parse_query("ADR-0002").exact_identifiers == ("ADR-0002",)

    def test_an_identifier_is_also_kept_as_a_term(self) -> None:
        # If the identifier finds nothing, the words should still be searched:
        # a guess about syntax must not silently narrow a search to nothing.
        assert parse_query("ADR-0002").terms == ("ADR-0002",)

    def test_an_ordinary_hyphenated_word_is_not_an_identifier(self) -> None:
        assert parse_query("mechanical-first").exact_identifiers == ()

    def test_an_empty_query_asks_for_nothing(self) -> None:
        assert parse_query("   ").is_empty


class TestResults:
    def test_a_result_carries_the_section_8_shape(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        document = make(documents, authority="authoritative", status="stable")
        indexer.reconcile()
        (result,) = search.search(SearchRequest(query="mechanical")).results
        assert result.concept_id == document.concept_id
        assert result.title == "Mechanical context"
        assert result.concept_type == "knowledge"
        assert str(result.path) == "30_Knowledge/Notes/thing.md"
        assert result.heading_path == ("Mechanical context",)
        assert "Mechanical acquisition" in result.excerpt
        assert result.line_range is not None
        assert (result.authority, result.status) == ("authoritative", "stable")
        assert result.rank == 1

    def test_every_result_says_why_it_was_included(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        # core/07 section 16, "Explainability".
        make(documents)
        indexer.reconcile()
        (result,) = search.search(SearchRequest(query="mechanical")).results
        assert result.reason.code == ReasonCode.FTS_RANK
        assert result.reason.is_mechanical
        assert result.retriever == "text"

    def test_an_exact_identifier_says_so(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        make(documents, body="# Decision\nSee ADR-0002 for the mechanical-first ruling.\n")
        indexer.reconcile()
        (result,) = search.search(SearchRequest(query="ADR-0002")).results
        assert result.reason.code == ReasonCode.EXACT_IDENTIFIER_MATCH

    def test_the_excerpt_comes_from_canonical_markdown_not_the_index(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        document = make(documents)
        indexer.reconcile()
        # Edit the vault without reindexing. The excerpt must follow the file.
        documents.put(
            StoredDocument(
                concept_id=document.concept_id,
                path=document.path,
                frontmatter=document.frontmatter,
                body="# Mechanical context\nRewritten prose entirely.\n",
            )
        )
        (result,) = search.search(SearchRequest(query="mechanical")).results
        assert "Rewritten prose" in result.excerpt

    def test_a_limit_is_respected(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        for n in range(4):
            make(documents, path=f"30_Knowledge/Notes/{n}.md")
        indexer.reconcile()
        assert len(search.search(SearchRequest(query="mechanical", limit=2)).results) == 2

    def test_metadata_filters_narrow_before_the_search(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        # core/07 Stage B: structural metadata excludes candidates before the
        # lexical stage runs, not after it.
        make(documents, path="30_Knowledge/Notes/a.md", tags=["architecture"])
        make(documents, path="30_Knowledge/Notes/b.md", tags=["cooking"])
        indexer.reconcile()
        response = search.search(SearchRequest(query="mechanical", tags=("architecture",)))
        assert [str(result.path) for result in response.results] == ["30_Knowledge/Notes/a.md"]

    def test_a_filter_that_matches_nothing_returns_nothing(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        make(documents)
        indexer.reconcile()
        assert search.search(SearchRequest(query="mechanical", tags=("nothing",))).results == ()

    def test_an_empty_query_returns_nothing_rather_than_everything(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        make(documents)
        indexer.reconcile()
        assert search.search(SearchRequest(query="   ")).results == ()


class TestStalenessIsReported:
    def test_a_stale_index_is_reported_not_repaired(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        make(documents)
        response = search.search(SearchRequest(query="mechanical"))
        assert response.index_is_stale
        assert response.results == ()
        assert indexer.health().indexed_documents == 0

    def test_a_current_index_is_not_reported_stale(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        make(documents)
        indexer.reconcile()
        assert not search.search(SearchRequest(query="mechanical")).index_is_stale

    def test_a_result_whose_document_moved_on_says_so(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        document = make(documents)
        indexer.reconcile()
        documents.put(
            StoredDocument(
                concept_id=document.concept_id,
                path=document.path,
                frontmatter=document.frontmatter,
                body="# Mechanical context\nRewritten prose entirely.\n",
            )
        )
        (result,) = search.search(SearchRequest(query="mechanical")).results
        assert result.chunk_has_changed


class TestConceptGet:
    def test_get_returns_canonical_content(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        document = make(documents)
        indexer.reconcile()
        view = search.get(document.concept_id)
        assert view is not None
        assert view.document.body == document.body
        assert view.record is not None
        assert view.record.title == "Mechanical context"

    def test_get_works_with_no_index_at_all(
        self, search: SearchService, documents: InMemoryDocumentStore
    ) -> None:
        # Markdown is canonical: reading a concept must not depend on a
        # projection existing (core/00 #1).
        document = make(documents)
        view = search.get(document.concept_id)
        assert view is not None
        assert view.record is None
        assert view.index_is_stale

    def test_get_reports_relations_in_both_directions(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        target = make(documents, path="30_Knowledge/Notes/other.md")
        source = make(
            documents, relations=[{"type": "depends_on", "target": str(target.concept_id)}]
        )
        indexer.reconcile()
        outgoing = search.get(source.concept_id)
        incoming = search.get(target.concept_id)
        assert outgoing is not None
        assert incoming is not None
        assert [n.concept_id for n in outgoing.outgoing] == [target.concept_id]
        assert [n.concept_id for n in incoming.incoming] == [source.concept_id]

    def test_get_of_an_unknown_identity_is_none(self, search: SearchService) -> None:
        assert search.get(ConceptId.new()) is None


class TestTheArchiveIsDetachedFromSearchToo:
    """Search excludes `90_Archive/`, as Context Packs do (core/07 section 3).

    Detachment is a property of the material, not of the interface reading it.
    Otherwise archived notes describing a superseded convention would rank
    above the specification in force. Archived material is reachable by
    opening it, not by searching for it.
    """

    def test_an_archived_document_is_not_a_result(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        make(documents, path="90_Archive/Knowledge/Notes/old.md", title="Superseded")
        indexer.reconcile()
        results = search.search(SearchRequest(query="mechanical"))
        assert results.results == ()

    def test_a_live_document_still_is(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        make(documents, path="30_Knowledge/Notes/current.md", title="In force")
        indexer.reconcile()
        found = search.search(SearchRequest(query="mechanical")).results
        paths = [str(result.path) for result in found]
        assert paths == ["30_Knowledge/Notes/current.md"]

    def test_the_archive_does_not_consume_the_limit(
        self, search: SearchService, documents: InMemoryDocumentStore, indexer: IndexService
    ) -> None:
        # The filter has to run before the limit, or an archive-heavy query
        # returns two live results out of a requested ten and looks like a
        # vault with nothing in it.
        for index in range(5):
            make(documents, path=f"90_Archive/Knowledge/Notes/old-{index}.md", title=f"Old {index}")
        for index in range(3):
            make(documents, path=f"30_Knowledge/Notes/live-{index}.md", title=f"Live {index}")
        indexer.reconcile()
        results = search.search(SearchRequest(query="mechanical", limit=3))
        assert len(results.results) == 3
        assert all(not str(result.path).startswith("90_Archive/") for result in results.results)
