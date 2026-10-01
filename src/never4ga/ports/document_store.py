"""DocumentStore -- access to canonical Markdown.

The canonical store is Markdown plus YAML frontmatter (core/00 #1). This port
exists so that core services never assume a filesystem layout, and so tests can
use an in-memory store rather than a temporary vault.

The v0.1 implementation is ``FileSystemMarkdownStore`` (core/05 section 21).
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from never4ga.domain.document import ForeignNote, StoredDocument, UntrackedMarkdown, VaultPath
from never4ga.domain.identity import ConceptId

__all__ = [
    "DocumentStore",
    "FileStat",
    "FileStatReportingStore",
    "ForeignMaterialStore",
    "IntegrityReportingStore",
    "StoreProblem",
]


@runtime_checkable
class DocumentStore(Protocol):
    """Canonical document access, addressed by stable identity."""

    def get(self, concept_id: ConceptId) -> StoredDocument | None:
        """Return the document with this identity, or ``None``."""
        ...

    def get_by_path(self, path: VaultPath) -> StoredDocument | None:
        """Return the document currently at this location, or ``None``."""
        ...

    def put(self, document: StoredDocument) -> None:
        """Create or replace a document.

        Writing an existing identity at a new path is a move: identity is
        preserved and the old location is vacated (core/02 section 5.1).
        Two distinct identities may not occupy one path.
        """
        ...

    def delete(self, concept_id: ConceptId) -> None:
        """Remove a document. Deleting an unknown identity is a no-op."""
        ...

    def iter_documents(self) -> Iterator[StoredDocument]:
        """Iterate every stored document."""
        ...

    def get_untracked(self, path: VaultPath) -> UntrackedMarkdown | None:
        """The Markdown at ``path`` that is not (yet) a concept.

        What adoption starts from: the file's partial frontmatter
        -- empty when it opens no block at all -- and its body, exactly as
        written. ``None`` when nothing is there.

        Raises :class:`~never4ga.errors.VaultIntegrityError` when the file
        already carries an ``id`` (it is a concept, or a broken one that
        `doctor` reports), and when a frontmatter block opens but cannot be
        parsed -- adoption starts from what a person wrote, and a file it
        cannot faithfully read is a file it must not rewrite.
        """
        ...


@dataclass(frozen=True, slots=True)
class StoreProblem:
    """A file that looks like a concept but cannot be treated as one."""

    path: VaultPath
    code: str
    detail: str


@runtime_checkable
class IntegrityReportingStore(Protocol):
    """A store that can say what is wrong with the material it holds.

    Optional, and separate from :class:`DocumentStore` on purpose. An in-memory
    store cannot hold a duplicate identity or unreadable YAML, so requiring
    every implementation to answer these questions would be requiring most of
    them to lie. ``doctor`` asks only stores that offer it.

    core/02 section 32 names duplicate UUIDs and missing IDs as first-class
    maintenance concerns; section 23 requires repair to stay explicit, so these
    are reports, never fixes.
    """

    def duplicate_ids(self) -> Mapping[ConceptId, tuple[VaultPath, ...]]:
        """Identities claimed by more than one document."""
        ...

    def problems(self) -> tuple[StoreProblem, ...]:
        """Files that look like concepts but cannot be read as one."""
        ...

    def untracked(self) -> tuple[VaultPath, ...]:
        """Concept-eligible Markdown that opens no frontmatter block.

        Prose is legitimate vault content, which is why these are absent from
        :meth:`DocumentStore.iter_documents` and from every staleness
        comparison -- but a hand-written note that *should* be a concept looks
        exactly the same, and `doctor` is where the difference is surfaced as
        the `untracked_document` warning rather than as silence.
        """
        ...


@dataclass(frozen=True, slots=True)
class FileStat:
    """What a store can say about a document without reading it."""

    path: VaultPath
    size: int
    modified_at: float


@runtime_checkable
class FileStatReportingStore(Protocol):
    """A store that can describe its documents without parsing them.

    Optional, and for one purpose. details/data-indexing-maintenance.md section
    13 allows ``path + size + mtime`` to "avoid unnecessary reads", and staleness
    reporting is where that matters: `search` and `get` report a stale index,
    and a read command cannot afford to parse the whole vault to do it.

    It is never the authority on change. Section 13 is explicit that filesystem
    mtime is not durable semantic provenance, so a *reconciliation* still
    compares content hashes; this only makes a cheap answer possible for a
    question asked on every read.
    """

    def iter_file_stats(self) -> Iterator[FileStat]:
        """Describe every concept-eligible document, without reading its content."""
        ...

    def file_stat(self, path: VaultPath) -> FileStat | None:
        """Describe one document without reading it, or ``None`` if it is not there.

        core/04 section 37 uses this for a declared context document the
        session was not given at startup: modified after the session opened
        means updated.
        A derived operational signal, as `core/02` allows -- never provenance.
        """
        ...

    def iter_foreign_file_stats(self, directories: Collection[str]) -> Iterator[FileStat]:
        """Describe every note :meth:`ForeignMaterialStore.iter_foreign_notes` would read.

        Every Markdown file the walk would consider, without opening one --
        so a concept that happens to sit in foreign material is described
        here too, and is told apart only when it is read.
        """
        ...


@runtime_checkable
class ForeignMaterialStore(Protocol):
    """A store that can read the notes in registered foreign material (core/02 section 3.3).

    Optional, like the capabilities above: it is what lets the indexer hold a
    pile by path before anything in it is adopted. ``directories`` are the
    top-level names `init` recorded in the system manifest; the store walks
    those and nothing else, and ignores a name that is a root or is not a
    single path segment. The registration is the caller's to read -- the
    store does not decide what is foreign.
    """

    def iter_foreign_notes(self, directories: Collection[str]) -> Iterator[ForeignNote]:
        """Every note under ``directories`` that is not a concept, in path order.

        Dot directories and dot files are not notes, and neither is a file
        carrying a Never4gA identity. A pile's own `index.md` or `log.md` is
        one: the names are reserved in Never4gA's structure, not in the
        writer's.
        """
        ...

    def get_foreign_note(self, path: VaultPath) -> ForeignNote | None:
        """The note at ``path``, or ``None`` if nothing is there or it is a concept.

        Registration is not checked: the caller asks only about paths a walk
        over registered directories produced.
        """
        ...
