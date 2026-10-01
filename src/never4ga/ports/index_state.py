"""IndexState -- what the index knows about its own currency.

Everything else in `never4ga.ports` describes a way of *finding* things. This
one describes the index's memory of its own work: which documents it has seen,
what they hashed to when it saw them, and where their Markdown links pointed.

It exists because two questions must be answerable without re-reading the vault.

**Is the index stale?** Reads report staleness rather than silently
reindexing. Reporting needs a record of what was indexed and from what content.

**What is broken?** details/data-indexing-maintenance.md section 21 lists broken
Markdown links among the first-class maintenance rules. A link that resolves
becomes a graph edge (section 11); a link that does not is the finding, and
something has to remember it.

Link records are deliberately *not* a retrieval capability. Nothing searches
them; `doctor` and `index --status` read them, and that is all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId

__all__ = ["IndexState", "IndexedDocument", "IndexedPath", "LinkRecord"]


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """One document as the index last saw it.

    ``file_size`` and ``modified_at`` are the cheap comparison of
    details/data-indexing-maintenance.md section 13 and may be absent -- a store
    that holds no files has neither. ``content_hash`` is the authoritative one:
    section 13 is explicit that filesystem mtime is never durable semantic
    provenance.
    """

    concept_id: ConceptId
    path: VaultPath
    content_hash: str
    indexed_at: str
    file_size: int | None = None
    modified_at: float | None = None


@dataclass(frozen=True, slots=True)
class IndexedPath:
    """A foreign note as the index last saw it (core/02 section 3.3).

    The same memory :class:`IndexedDocument` keeps, keyed by the only thing a
    foreign note has: where it sits. It is a derived record, never an
    identity. A rename is a new record and the old one forgotten.
    """

    path: VaultPath
    content_hash: str
    indexed_at: str
    file_size: int | None = None
    modified_at: float | None = None


@dataclass(frozen=True, slots=True)
class LinkRecord:
    """One vault-internal link, and whether it found anything.

    A link addresses its target one of two ways, and exactly one of these is
    set. ``target_path`` is a location, resolved against the document that
    contains it -- known even when nothing is there. ``target_name`` is a bare
    ``[[Note]]``, which names a document without saying where it lives.

    ``target_id`` is absent exactly when the link resolved to nothing, which is
    the broken-link finding of details/data-indexing-maintenance.md section 21.

    External links are not recorded. They are never broken, never traversed and
    never searched, so a row for one would be noise in the only table anything
    reads to find what is wrong.
    """

    source: ConceptId
    target_path: VaultPath | None = None
    target_name: str | None = None
    target_id: ConceptId | None = None
    anchor: str | None = None

    def __post_init__(self) -> None:
        if self.target_path is None and not self.target_name:
            raise ValueError("a link record needs either a target path or a target name")

    @property
    def is_broken(self) -> bool:
        """The link addressed something, and no document is there."""
        return self.target_id is None

    @property
    def target(self) -> str:
        """How to describe the target to a person: its path, or the name written."""
        return str(self.target_path) if self.target_path is not None else (self.target_name or "")


@runtime_checkable
class IndexState(Protocol):
    def record(self, document: IndexedDocument) -> None:
        """Note that this document has been indexed, replacing any earlier note."""
        ...

    def forget(self, concept_id: ConceptId) -> None:
        """Forget a document and its links. Forgetting an unknown one is a no-op."""
        ...

    def get(self, concept_id: ConceptId) -> IndexedDocument | None: ...

    def indexed_documents(self) -> Sequence[IndexedDocument]: ...

    def replace_links(self, source: ConceptId, links: Sequence[LinkRecord]) -> None:
        """Replace every link recorded for this document."""
        ...

    def links(self) -> Sequence[LinkRecord]: ...

    def record_path(self, record: IndexedPath) -> None:
        """Note that the foreign note at this path has been indexed, replacing any earlier note.

        Kept apart from :meth:`record`: nothing that reads concept records may
        ever meet a path standing in for a concept.
        """
        ...

    def forget_path(self, path: VaultPath) -> None:
        """Forget a foreign note. Forgetting an unknown one is a no-op."""
        ...

    def indexed_paths(self) -> Sequence[IndexedPath]:
        """Every foreign note recorded, in path order."""
        ...

    def clear(self) -> None:
        """Discard the whole record so it can be rebuilt from canonical sources.

        Concept records, links and path records alike.
        """
        ...
