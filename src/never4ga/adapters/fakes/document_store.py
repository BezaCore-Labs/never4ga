"""In-memory DocumentStore."""

from __future__ import annotations

from collections.abc import Collection, Iterator

from never4ga.domain.document import ForeignNote, StoredDocument, UntrackedMarkdown, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.layout import is_foreign_note

__all__ = ["InMemoryDocumentStore"]


class InMemoryDocumentStore:
    """A DocumentStore backed by dictionaries.

    Used by contract tests and by higher-level tests that need canonical
    documents without a temporary vault on disk.
    """

    def __init__(self) -> None:
        self._by_id: dict[ConceptId, StoredDocument] = {}
        self._id_by_path: dict[VaultPath, ConceptId] = {}
        self._untracked: dict[VaultPath, UntrackedMarkdown] = {}

    def get(self, concept_id: ConceptId) -> StoredDocument | None:
        return self._by_id.get(concept_id)

    def get_by_path(self, path: VaultPath) -> StoredDocument | None:
        concept_id = self._id_by_path.get(path)
        return self._by_id.get(concept_id) if concept_id is not None else None

    def put(self, document: StoredDocument) -> None:
        occupant = self._id_by_path.get(document.path)
        if occupant is not None and occupant != document.concept_id:
            raise ValueError(
                f"path {document.path} is already occupied by {occupant}; "
                "two documents cannot share one location"
            )
        previous = self._by_id.get(document.concept_id)
        if previous is not None and previous.path != document.path:
            # A move: identity is preserved, the old location is vacated.
            self._id_by_path.pop(previous.path, None)
        self._by_id[document.concept_id] = document
        self._id_by_path[document.path] = document.concept_id
        # Writing a concept where prose sat is adoption completing.
        self._untracked.pop(document.path, None)

    def delete(self, concept_id: ConceptId) -> None:
        document = self._by_id.pop(concept_id, None)
        if document is not None:
            self._id_by_path.pop(document.path, None)

    def iter_documents(self) -> Iterator[StoredDocument]:
        return iter(list(self._by_id.values()))

    def get_untracked(self, path: VaultPath) -> UntrackedMarkdown | None:
        return self._untracked.get(path)

    def place_untracked(self, untracked: UntrackedMarkdown) -> None:
        """Seed hand-written Markdown, the way a person's editor would."""
        self._untracked[untracked.path] = untracked

    def remove_untracked(self, path: VaultPath) -> None:
        """Delete hand-written Markdown, the way a person would."""
        self._untracked.pop(path, None)

    def iter_foreign_notes(self, directories: Collection[str]) -> Iterator[ForeignNote]:
        """The seeded hand-written Markdown that sits in foreign material."""
        for path in sorted(self._untracked):
            if is_foreign_note(path, directories):
                note = self.get_foreign_note(path)
                assert note is not None
                yield note

    def get_foreign_note(self, path: VaultPath) -> ForeignNote | None:
        untracked = self._untracked.get(path)
        if untracked is None:
            return None
        return ForeignNote(path=path, frontmatter=untracked.frontmatter, body=untracked.body)
