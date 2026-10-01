"""IndexState contract.

core/06 section 22: every backend interface gets one reusable suite, and an
implementation becomes supported by passing it.
"""

from __future__ import annotations

import pytest

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.index_state import IndexedDocument, IndexedPath, IndexState, LinkRecord

SOURCE = ConceptId.new()


def make_indexed(
    concept_id: ConceptId | None = SOURCE,
    *,
    path: str = "30_Knowledge/Notes/thing.md",
    content_hash: str = "abc123",
    indexed_at: str = "2026-08-23T12:00:00Z",
    file_size: int | None = None,
    modified_at: float | None = None,
) -> IndexedDocument:
    return IndexedDocument(
        concept_id=concept_id or ConceptId.new(),
        path=VaultPath.parse(path),
        content_hash=content_hash,
        indexed_at=indexed_at,
        file_size=file_size,
        modified_at=modified_at,
    )


def _link_to(
    path: str, *, target_id: ConceptId | None = None, anchor: str | None = None
) -> LinkRecord:
    return LinkRecord(
        source=SOURCE, target_path=VaultPath.parse(path), target_id=target_id, anchor=anchor
    )


class IndexStateContract:
    @pytest.fixture
    def state(self) -> IndexState:
        raise NotImplementedError("supply an IndexState fixture")

    def test_record_then_get(self, state: IndexState) -> None:
        indexed = make_indexed()
        state.record(indexed)
        loaded = state.get(indexed.concept_id)
        assert loaded is not None
        assert loaded.content_hash == "abc123"
        assert loaded.path == indexed.path

    def test_record_replaces_rather_than_duplicates(self, state: IndexState) -> None:
        first = make_indexed(content_hash="one")
        state.record(first)
        state.record(make_indexed(first.concept_id, content_hash="two"))
        loaded = state.get(first.concept_id)
        assert loaded is not None
        assert loaded.content_hash == "two"
        assert len(state.indexed_documents()) == 1

    def test_a_move_keeps_identity_and_updates_the_path(self, state: IndexState) -> None:
        # details/data-indexing-maintenance.md section 14: same UUID, new path,
        # same concept. A move must never mint a second entry.
        indexed = make_indexed()
        state.record(indexed)
        state.record(make_indexed(indexed.concept_id, path="90_Archive/Knowledge/Notes/thing.md"))
        loaded = state.get(indexed.concept_id)
        assert loaded is not None
        assert str(loaded.path) == "90_Archive/Knowledge/Notes/thing.md"
        assert len(state.indexed_documents()) == 1

    def test_get_missing_returns_none(self, state: IndexState) -> None:
        assert state.get(ConceptId.new()) is None

    def test_the_cheap_comparison_fields_round_trip(self, state: IndexState) -> None:
        indexed = make_indexed(file_size=1234, modified_at=1750000000.5)
        state.record(indexed)
        loaded = state.get(indexed.concept_id)
        assert loaded is not None
        assert (loaded.file_size, loaded.modified_at) == (1234, 1750000000.5)

    def test_they_may_be_absent(self, state: IndexState) -> None:
        # A store that holds no files has neither.
        indexed = make_indexed()
        state.record(indexed)
        loaded = state.get(indexed.concept_id)
        assert loaded is not None
        assert (loaded.file_size, loaded.modified_at) == (None, None)

    def test_forget_removes_the_document(self, state: IndexState) -> None:
        indexed = make_indexed()
        state.record(indexed)
        state.forget(indexed.concept_id)
        assert state.get(indexed.concept_id) is None

    def test_forgetting_an_unknown_document_is_a_no_op(self, state: IndexState) -> None:
        state.forget(ConceptId.new())
        assert state.indexed_documents() == ()

    def test_links_are_recorded_against_their_source(self, state: IndexState) -> None:
        indexed = make_indexed()
        state.record(indexed)
        state.replace_links(indexed.concept_id, [_link_to("30_Knowledge/Notes/other.md")])
        (link,) = state.links()
        assert link.source == indexed.concept_id
        assert str(link.target_path) == "30_Knowledge/Notes/other.md"

    def test_replacing_links_discards_the_previous_set(self, state: IndexState) -> None:
        indexed = make_indexed()
        state.record(indexed)
        state.replace_links(indexed.concept_id, [_link_to("30_Knowledge/Notes/old.md")])
        state.replace_links(indexed.concept_id, [_link_to("30_Knowledge/Notes/new.md")])
        assert [str(link.target_path) for link in state.links()] == ["30_Knowledge/Notes/new.md"]

    def test_a_resolved_link_keeps_its_target_identity(self, state: IndexState) -> None:
        indexed = make_indexed()
        target = ConceptId.new()
        state.record(indexed)
        state.replace_links(
            indexed.concept_id,
            [_link_to("30_Knowledge/Notes/other.md", target_id=target, anchor="a-heading")],
        )
        (link,) = state.links()
        assert link.target_id == target
        assert link.anchor == "a-heading"
        assert not link.is_broken

    def test_a_link_to_nothing_is_reported_as_broken(self, state: IndexState) -> None:
        # details/data-indexing-maintenance.md section 21: a broken Markdown link
        # is a first-class maintenance finding, and this is where it is found.
        indexed = make_indexed()
        state.record(indexed)
        state.replace_links(indexed.concept_id, [_link_to("30_Knowledge/Notes/missing.md")])
        assert [link.is_broken for link in state.links()] == [True]

    def test_forgetting_a_document_forgets_its_links(self, state: IndexState) -> None:
        indexed = make_indexed()
        state.record(indexed)
        state.replace_links(indexed.concept_id, [_link_to("30_Knowledge/Notes/other.md")])
        state.forget(indexed.concept_id)
        assert state.links() == ()

    def test_a_link_may_name_its_target_instead_of_locating_it(self, state: IndexState) -> None:
        # A bare `[[Note]]` names a document without saying where it lives.
        indexed = make_indexed()
        state.record(indexed)
        state.replace_links(
            indexed.concept_id, [LinkRecord(source=SOURCE, target_name="Other Note")]
        )
        (link,) = state.links()
        assert link.target_name == "Other Note"
        assert link.target_path is None
        assert link.is_broken

    def test_a_named_link_that_resolved_keeps_both(self, state: IndexState) -> None:
        indexed = make_indexed()
        target = ConceptId.new()
        state.record(indexed)
        state.replace_links(
            indexed.concept_id,
            [LinkRecord(source=SOURCE, target_name="Other Note", target_id=target)],
        )
        (link,) = state.links()
        assert (link.target_name, link.target_id) == ("Other Note", target)
        assert not link.is_broken

    def test_a_link_must_address_something(self, state: IndexState) -> None:
        with pytest.raises(ValueError, match="target"):
            LinkRecord(source=SOURCE)

    def test_the_target_reads_back_as_written(self, state: IndexState) -> None:
        assert LinkRecord(source=SOURCE, target_name="Other Note").target == "Other Note"
        assert LinkRecord(source=SOURCE, target_path=VaultPath.parse("a/b.md")).target == "a/b.md"

    def test_clear_makes_the_record_disposable(self, state: IndexState) -> None:
        # core/06 section 2: every projection must be rebuildable from Markdown.
        indexed = make_indexed()
        state.record(indexed)
        state.replace_links(indexed.concept_id, [_link_to("30_Knowledge/Notes/other.md")])
        state.clear()
        assert state.indexed_documents() == ()
        assert state.links() == ()

    def test_identity_is_a_uuid_not_a_backend_row(self, state: IndexState) -> None:
        indexed = make_indexed()
        state.record(indexed)
        (loaded,) = state.indexed_documents()
        assert isinstance(loaded.concept_id, ConceptId)


FOREIGN = VaultPath.parse("Projects/garden/raised-beds.md")


def make_path_record(
    path: VaultPath = FOREIGN,
    *,
    content_hash: str = "abc123",
    indexed_at: str = "2026-09-17T12:00:00Z",
    file_size: int | None = None,
    modified_at: float | None = None,
) -> IndexedPath:
    return IndexedPath(
        path=path,
        content_hash=content_hash,
        indexed_at=indexed_at,
        file_size=file_size,
        modified_at=modified_at,
    )


class PathRecordContract:
    """A foreign note, remembered by its path.

    A foreign note has no identity, so the index's memory of it is keyed by
    where it sits. The record is derived and never an identity: it is kept
    apart from the concept records so that nothing reading those -- the
    metadata projection shares their table in SQLite -- ever meets a path
    pretending to be a concept.
    """

    @pytest.fixture
    def state(self) -> IndexState:
        raise NotImplementedError("supply an IndexState fixture")

    def test_a_path_record_reads_back(self, state: IndexState) -> None:
        state.record_path(make_path_record(file_size=12, modified_at=1750000000.5))
        (loaded,) = state.indexed_paths()
        assert loaded == make_path_record(file_size=12, modified_at=1750000000.5)

    def test_recording_a_path_again_replaces_it(self, state: IndexState) -> None:
        state.record_path(make_path_record(content_hash="one"))
        state.record_path(make_path_record(content_hash="two"))
        assert [record.content_hash for record in state.indexed_paths()] == ["two"]

    def test_paths_read_back_in_path_order(self, state: IndexState) -> None:
        state.record_path(make_path_record(VaultPath.parse("Reading/b.md")))
        state.record_path(make_path_record(VaultPath.parse("Projects/a.md")))
        assert [str(r.path) for r in state.indexed_paths()] == ["Projects/a.md", "Reading/b.md"]

    def test_forget_path_removes_it(self, state: IndexState) -> None:
        state.record_path(make_path_record())
        state.forget_path(FOREIGN)
        assert state.indexed_paths() == ()

    def test_forgetting_an_unknown_path_is_a_no_op(self, state: IndexState) -> None:
        state.forget_path(FOREIGN)
        assert state.indexed_paths() == ()

    def test_a_path_record_is_not_a_concept_record(self, state: IndexState) -> None:
        state.record_path(make_path_record())
        assert state.indexed_documents() == ()

    def test_forgetting_a_concept_leaves_path_records_alone(self, state: IndexState) -> None:
        indexed = make_indexed()
        state.record(indexed)
        state.record_path(make_path_record())
        state.forget(indexed.concept_id)
        assert [record.path for record in state.indexed_paths()] == [FOREIGN]

    def test_clear_discards_path_records_too(self, state: IndexState) -> None:
        state.record_path(make_path_record())
        state.clear()
        assert state.indexed_paths() == ()
