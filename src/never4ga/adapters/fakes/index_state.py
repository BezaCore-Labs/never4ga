"""In-memory IndexState.

Lets a service test run the whole indexing pipeline with no database at all,
which is what keeps the pipeline honest about depending on ports rather than on
SQLite (core/06 section 25 test A).
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.index_state import IndexedDocument, IndexedPath, LinkRecord

__all__ = ["InMemoryIndexState"]


class InMemoryIndexState:
    def __init__(self) -> None:
        self._documents: dict[ConceptId, IndexedDocument] = {}
        self._links: dict[ConceptId, tuple[LinkRecord, ...]] = {}
        self._paths: dict[VaultPath, IndexedPath] = {}

    def record(self, document: IndexedDocument) -> None:
        self._documents[document.concept_id] = document

    def forget(self, concept_id: ConceptId) -> None:
        self._documents.pop(concept_id, None)
        self._links.pop(concept_id, None)

    def get(self, concept_id: ConceptId) -> IndexedDocument | None:
        return self._documents.get(concept_id)

    def indexed_documents(self) -> Sequence[IndexedDocument]:
        return tuple(self._documents[key] for key in sorted(self._documents))

    def replace_links(self, source: ConceptId, links: Sequence[LinkRecord]) -> None:
        self._links[source] = tuple(links)

    def links(self) -> Sequence[LinkRecord]:
        return tuple(link for key in sorted(self._links) for link in self._links[key])

    def record_path(self, record: IndexedPath) -> None:
        self._paths[record.path] = record

    def forget_path(self, path: VaultPath) -> None:
        self._paths.pop(path, None)

    def indexed_paths(self) -> Sequence[IndexedPath]:
        return tuple(self._paths[key] for key in sorted(self._paths))

    def clear(self) -> None:
        self._documents.clear()
        self._links.clear()
        self._paths.clear()
