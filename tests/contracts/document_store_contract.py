"""DocumentStore contract (core/05 section 20, core/00 #1 and #15)."""

from __future__ import annotations

import pytest

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.document_store import DocumentStore


def make_document(
    concept_id: ConceptId | None = None,
    path: str = "30_Knowledge/Notes/thing.md",
    body: str = "# Thing\n",
    **extra_frontmatter: object,
) -> StoredDocument:
    concept_id = concept_id or ConceptId.new()
    frontmatter: dict[str, object] = {
        "type": "knowledge",
        "id": str(concept_id),
        "schema": "never4ga/0.1",
        "title": "Thing",
        "created_at": "2026-08-22T19:45:00Z",
    }
    frontmatter.update(extra_frontmatter)
    return StoredDocument(
        concept_id=concept_id,
        path=VaultPath.parse(path),
        frontmatter=frontmatter,
        body=body,
    )


class DocumentStoreContract:
    """Behaviour every DocumentStore implementation must exhibit."""

    @pytest.fixture
    def store(self) -> DocumentStore:
        raise NotImplementedError("supply a DocumentStore fixture")

    def test_put_then_get_round_trips(self, store: DocumentStore) -> None:
        document = make_document()
        store.put(document)
        loaded = store.get(document.concept_id)
        assert loaded is not None
        assert loaded.concept_id == document.concept_id
        assert loaded.body == document.body
        assert loaded.path == document.path

    def test_unknown_extension_fields_survive_the_round_trip(self, store: DocumentStore) -> None:
        document = make_document(x_vendor={"nested": [1, 2]}, future_field="keep")
        store.put(document)
        loaded = store.get(document.concept_id)
        assert loaded is not None
        assert loaded.frontmatter["x_vendor"] == {"nested": [1, 2]}
        assert loaded.frontmatter["future_field"] == "keep"

    def test_get_missing_returns_none(self, store: DocumentStore) -> None:
        assert store.get(ConceptId.new()) is None

    def test_get_by_path(self, store: DocumentStore) -> None:
        document = make_document()
        store.put(document)
        loaded = store.get_by_path(VaultPath.parse("30_Knowledge/Notes/thing.md"))
        assert loaded is not None
        assert loaded.concept_id == document.concept_id

    def test_get_by_unknown_path_returns_none(self, store: DocumentStore) -> None:
        assert store.get_by_path(VaultPath.parse("90_Archive/nope.md")) is None

    def test_identity_survives_a_move(self, store: DocumentStore) -> None:
        # core/02 section 5.1: id must not change when a file is renamed or moved.
        document = make_document()
        store.put(document)
        moved = make_document(concept_id=document.concept_id, path="90_Archive/thing.md")
        store.put(moved)

        assert store.get_by_path(VaultPath.parse("30_Knowledge/Notes/thing.md")) is None
        relocated = store.get_by_path(VaultPath.parse("90_Archive/thing.md"))
        assert relocated is not None
        assert relocated.concept_id == document.concept_id
        assert store.get(document.concept_id) is not None

    def test_two_documents_cannot_occupy_one_path(self, store: DocumentStore) -> None:
        store.put(make_document())
        with pytest.raises(ValueError, match="path"):
            store.put(make_document(path="30_Knowledge/Notes/thing.md"))

    def test_delete_removes_by_identity_and_path(self, store: DocumentStore) -> None:
        document = make_document()
        store.put(document)
        store.delete(document.concept_id)
        assert store.get(document.concept_id) is None
        assert store.get_by_path(document.path) is None

    def test_delete_is_idempotent(self, store: DocumentStore) -> None:
        store.delete(ConceptId.new())

    def test_iter_documents_yields_everything_stored(self, store: DocumentStore) -> None:
        first = make_document(path="30_Knowledge/Notes/a.md")
        second = make_document(path="30_Knowledge/Notes/b.md")
        store.put(first)
        store.put(second)
        assert {d.concept_id for d in store.iter_documents()} == {
            first.concept_id,
            second.concept_id,
        }

    def test_empty_store_iterates_empty(self, store: DocumentStore) -> None:
        assert list(store.iter_documents()) == []
