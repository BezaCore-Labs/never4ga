"""TextIndex contract (core/06 section 18, core/07 Stage D)."""

from __future__ import annotations

import hashlib

import pytest

from never4ga.domain.capabilities import TextIndexCapability
from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import ReasonCode
from never4ga.ports.text_index import IndexedChunk, TextIndex, TextQuery


def make_chunk(
    text: str,
    *,
    concept_id: ConceptId | None = None,
    heading_path: tuple[str, ...] = ("Thing",),
    ordinal: int = 0,
    path: str = "30_Knowledge/Notes/thing.md",
) -> IndexedChunk:
    concept_id = concept_id or ConceptId.new()
    return IndexedChunk(
        chunk=ChunkIdentity(
            concept_id=concept_id,
            heading_path=heading_path,
            ordinal=ordinal,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            policy_version="chunk/0.1",
        ),
        path=VaultPath.parse(path),
        text=text,
        line_range=LineRange(start=1, end=10),
    )


def make_path_chunk(
    text: str,
    *,
    path: str = "Projects/garden/raised-beds.md",
    heading_path: tuple[str, ...] = ("Raised beds",),
    ordinal: int = 0,
) -> IndexedChunk:
    """A chunk owned by its path: a foreign note with no concept (core/01 section 1)."""
    source = VaultPath.parse(path)
    return IndexedChunk(
        chunk=ChunkIdentity(
            concept_id=None,
            source=source,
            heading_path=heading_path,
            ordinal=ordinal,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            policy_version="chunk/0.1",
        ),
        path=source,
        text=text,
        line_range=LineRange(start=1, end=10),
    )


class TextIndexContract:
    @pytest.fixture
    def index(self) -> TextIndex:
        raise NotImplementedError("supply a TextIndex fixture")

    def test_declares_capabilities(self, index: TextIndex) -> None:
        assert TextIndexCapability.EXACT_IDENTIFIERS in index.capabilities

    def test_indexed_term_is_findable(self, index: TextIndex) -> None:
        chunk = make_chunk("Mechanical context acquisition beats guessing.")
        index.index_chunk(chunk)
        results = index.search(TextQuery(terms=("mechanical",)))
        assert [c.concept_id for c in results] == [chunk.chunk.concept_id]

    def test_search_is_case_insensitive(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("Workspace Resolution"))
        assert index.search(TextQuery(terms=("WORKSPACE",)))

    def test_result_carries_lexical_provenance(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("workspace resolution"))
        (result,) = index.search(TextQuery(terms=("workspace",)))
        assert result.reason.code == ReasonCode.FTS_RANK
        assert result.reason.is_mechanical
        assert result.retriever == "text"

    def test_result_carries_chunk_and_path(self, index: TextIndex) -> None:
        chunk = make_chunk("workspace resolution")
        index.index_chunk(chunk)
        (result,) = index.search(TextQuery(terms=("workspace",)))
        assert result.chunk == chunk.chunk
        assert result.source_path == chunk.path

    def test_ranks_are_one_based_and_contiguous(self, index: TextIndex) -> None:
        for i in range(3):
            index.index_chunk(make_chunk("workspace " * (i + 1), path=f"30_Knowledge/Notes/{i}.md"))
        results = index.search(TextQuery(terms=("workspace",)))
        assert [c.rank for c in results] == list(range(1, len(results) + 1))

    def test_exact_identifier_match_is_reported_as_such(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("See ADR-0002 for the mechanical-first decision."))
        (result,) = index.search(TextQuery(exact_identifiers=("ADR-0002",)))
        assert result.reason.code == ReasonCode.EXACT_IDENTIFIER_MATCH

    def test_exact_identifier_does_not_match_a_substring(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("ADR-00021 is a different document"))
        assert index.search(TextQuery(exact_identifiers=("ADR-0002",))) == ()

    def test_exact_identifiers_outrank_term_matches(self, index: TextIndex) -> None:
        loose = make_chunk("adr adr adr adr adr", path="30_Knowledge/Notes/loose.md")
        exact = make_chunk("ADR-0002 adr", path="30_Knowledge/Notes/exact.md")
        index.index_chunk(loose)
        index.index_chunk(exact)
        results = index.search(TextQuery(terms=("adr",), exact_identifiers=("ADR-0002",)))
        assert results[0].concept_id == exact.chunk.concept_id

    def test_unknown_term_returns_nothing(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("workspace resolution"))
        assert index.search(TextQuery(terms=("qdrant",))) == ()

    def test_empty_query_returns_nothing(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("workspace resolution"))
        assert index.search(TextQuery()) == ()

    def test_limit_is_respected(self, index: TextIndex) -> None:
        for i in range(5):
            index.index_chunk(make_chunk("workspace", path=f"30_Knowledge/Notes/{i}.md"))
        assert len(index.search(TextQuery(terms=("workspace",), limit=2))) == 2

    def test_scope_filter_restricts_to_a_concept_set(self, index: TextIndex) -> None:
        wanted = make_chunk("workspace", path="30_Knowledge/Notes/a.md")
        unwanted = make_chunk("workspace", path="30_Knowledge/Notes/b.md")
        index.index_chunk(wanted)
        index.index_chunk(unwanted)
        assert wanted.chunk.concept_id is not None
        results = index.search(
            TextQuery(terms=("workspace",), concept_ids=(wanted.chunk.concept_id,))
        )
        assert [c.concept_id for c in results] == [wanted.chunk.concept_id]

    def test_remove_document_drops_all_its_chunks(self, index: TextIndex) -> None:
        concept_id = ConceptId.new()
        index.index_chunk(make_chunk("workspace one", concept_id=concept_id, ordinal=0))
        index.index_chunk(make_chunk("workspace two", concept_id=concept_id, ordinal=1))
        index.remove_document(concept_id)
        assert index.search(TextQuery(terms=("workspace",))) == ()

    def test_reindexing_the_same_chunk_does_not_duplicate(self, index: TextIndex) -> None:
        chunk = make_chunk("workspace resolution")
        index.index_chunk(chunk)
        index.index_chunk(chunk)
        assert len(index.search(TextQuery(terms=("workspace",)))) == 1

    def test_clear_makes_the_projection_disposable(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("workspace"))
        index.clear()
        assert index.search(TextQuery(terms=("workspace",))) == ()

    # -- path-owned chunks (core/01 section 1) ---------------------------

    def test_a_path_owned_chunk_is_findable(self, index: TextIndex) -> None:
        index.index_chunk(make_path_chunk("compost ratios and the spring soil test"))
        found = index.search(TextQuery(terms=("compost",)))
        assert len(found) == 1
        assert found[0].concept_id is None
        assert found[0].source_path == VaultPath.parse("Projects/garden/raised-beds.md")
        assert found[0].reason.code == ReasonCode.FTS_RANK
        assert found[0].chunk is not None and found[0].chunk.source == found[0].source_path

    def test_a_path_owned_chunk_has_no_concept_and_says_so(self, index: TextIndex) -> None:
        index.index_chunk(make_path_chunk("compost ratios"))
        (found,) = index.search(TextQuery(terms=("compost",)))
        assert found.chunk is not None
        assert found.chunk.concept_id is None
        assert found.chunk.owner == "path:Projects/garden/raised-beds.md"

    def test_a_scope_of_concepts_excludes_path_owned_chunks(self, index: TextIndex) -> None:
        # Focused context reduces to a set of concepts first (core/07 Stage
        # B); a chunk that belongs to no concept is outside any such set.
        concept = ConceptId.new()
        index.index_chunk(make_chunk("compost from a concept", concept_id=concept))
        index.index_chunk(make_path_chunk("compost from a pile"))
        scoped = index.search(TextQuery(terms=("compost",), concept_ids=(concept,)))
        assert [candidate.concept_id for candidate in scoped] == [concept]

    def test_a_scope_can_admit_path_owned_chunks_as_well(self, index: TextIndex) -> None:
        # A pile belongs to no workspace, so a focused pack reduces to its
        # concepts *and* the pile -- never to another workspace's concepts.
        concept = ConceptId.new()
        index.index_chunk(make_chunk("compost from a concept", concept_id=concept))
        index.index_chunk(make_chunk("compost from elsewhere", concept_id=ConceptId.new()))
        index.index_chunk(make_path_chunk("compost from a pile"))
        scoped = index.search(
            TextQuery(terms=("compost",), concept_ids=(concept,), include_paths=True)
        )
        assert sorted(str(candidate.concept_id) for candidate in scoped) == sorted(
            [str(concept), "None"]
        )

    def test_admitting_paths_without_a_scope_changes_nothing(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("compost from a concept"))
        index.index_chunk(make_path_chunk("compost from a pile"))
        assert len(index.search(TextQuery(terms=("compost",), include_paths=True))) == 2

    def test_without_a_scope_both_kinds_are_found(self, index: TextIndex) -> None:
        index.index_chunk(make_chunk("compost from a concept"))
        index.index_chunk(make_path_chunk("compost from a pile"))
        assert len(index.search(TextQuery(terms=("compost",)))) == 2

    def test_remove_path_drops_only_that_paths_chunks(self, index: TextIndex) -> None:
        index.index_chunk(make_path_chunk("compost one", ordinal=0))
        index.index_chunk(make_path_chunk("compost two", ordinal=1))
        index.index_chunk(make_path_chunk("compost elsewhere", path="Reading/book.md"))
        index.index_chunk(make_chunk("compost from a concept"))
        index.remove_path(VaultPath.parse("Projects/garden/raised-beds.md"))
        left = index.search(TextQuery(terms=("compost",)))
        assert sorted(str(candidate.source_path) for candidate in left) == [
            "30_Knowledge/Notes/thing.md",
            "Reading/book.md",
        ]

    def test_remove_document_leaves_path_owned_chunks_alone(self, index: TextIndex) -> None:
        concept = ConceptId.new()
        index.index_chunk(make_chunk("compost from a concept", concept_id=concept))
        index.index_chunk(make_path_chunk("compost from a pile"))
        index.remove_document(concept)
        left = index.search(TextQuery(terms=("compost",)))
        assert [candidate.concept_id for candidate in left] == [None]

    def test_remove_path_does_not_touch_a_concept_at_that_path(self, index: TextIndex) -> None:
        # A concept's chunks are owned by its id, whatever path it sits at.
        index.index_chunk(
            make_chunk("compost from a concept", path="Projects/garden/raised-beds.md")
        )
        index.remove_path(VaultPath.parse("Projects/garden/raised-beds.md"))
        assert len(index.search(TextQuery(terms=("compost",)))) == 1

    def test_reindexing_a_path_owned_chunk_does_not_duplicate(self, index: TextIndex) -> None:
        index.index_chunk(make_path_chunk("compost ratios"))
        index.index_chunk(make_path_chunk("compost ratios"))
        assert len(index.search(TextQuery(terms=("compost",)))) == 1
        assert len(index.chunk_keys()) == 1

    def test_removing_a_path_that_was_never_indexed_is_not_an_error(self, index: TextIndex) -> None:
        index.remove_path(VaultPath.parse("Nowhere/nothing.md"))
