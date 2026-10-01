"""Keeping the derived indexes current.

details/data-indexing-maintenance.md section 12 is the pipeline; sections 16 and
17 are the guarantees. Every projection this service writes is derived from
Markdown and rebuildable from it, so `rebuild` after deleting the database must
recover everything (section 17).

Three rules shape the code.

**Reads report; they never repair.** A stale index is reported, and reindexing is
something the user asks for. Silent reindex-on-read would make every read slow,
non-deterministic and quietly divergent, which is the opposite of a system whose value
is bounded, predictable behaviour. :meth:`IndexService.health` writes nothing.

**A bad document is reported, not skipped.** Section 12. One malformed relation
must not cost a note its searchability, so projection issues travel with the run
and the document is indexed anyway.

**Identity is the key, never the path.** Section 14: a document whose path
changed but whose UUID did not is the same concept in a new place. Reconciling
by identity is what makes rename detection free rather than a heuristic.

There is one exception. A note in registered foreign material has no identity
(core/01 section 1), so it is held by its path -- in the text index only,
never in a metadata or graph projection -- and a rename is exactly what a
path-keyed derived record should make of it: a new record, and the old one
gone.

It depends on ports only, so it runs against the in-memory fakes with no
database present (core/06 section 25 test A).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace

from never4ga.domain.document import ForeignNote, StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.relations import LINK_RELATION, PARENT_RELATION
from never4ga.errors import IdentityError
from never4ga.indexing.chunks import build_chunks
from never4ga.indexing.projection import (
    DocumentProjection,
    ProjectionIssue,
    document_names,
    project,
    project_foreign,
    record_names,
)
from never4ga.layout import RESERVED_INDEX, SYSTEM_MANIFEST, registered_foreign_material
from never4ga.ports.document_store import (
    DocumentStore,
    FileStatReportingStore,
    ForeignMaterialStore,
)
from never4ga.ports.graph_index import Direction, GraphEdge, GraphIndex
from never4ga.ports.index_state import IndexedDocument, IndexedPath, IndexState, LinkRecord
from never4ga.ports.metadata_index import MetadataIndex, MetadataQuery, MetadataRecord
from never4ga.ports.text_index import IndexedChunk, TextIndex
from never4ga.services.authoring import Clock, format_timestamp, utc_now

__all__ = [
    "IndexHealth",
    "IndexRun",
    "IndexService",
    "UnresolvedRelation",
]


@dataclass(frozen=True, slots=True)
class IndexRun:
    """What one reconciliation did.

    Concepts and foreign notes are counted apart: a note held by its path is
    not a document the vault knows by identity, and a count that mixed them
    would say the vault holds concepts it does not.
    """

    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    issues: tuple[ProjectionIssue, ...] = field(default_factory=tuple)
    foreign_indexed: int = 0
    foreign_unchanged: int = 0
    foreign_removed: int = 0


@dataclass(frozen=True, slots=True)
class UnresolvedRelation:
    """A typed relation whose target is not a document in this vault."""

    source: ConceptId
    relation_type: str
    target: ConceptId


@dataclass(frozen=True, slots=True)
class IndexHealth:
    """What the index knows, and what it has stopped knowing."""

    indexed_documents: int = 0
    last_indexed_at: str | None = None
    new: tuple[VaultPath, ...] = ()
    changed: tuple[VaultPath, ...] = ()
    missing: tuple[VaultPath, ...] = ()
    #: The identity the index recorded at each missing path.
    #:
    #: A path goes missing for two very different reasons: the file was
    #: deleted, or it is still there and stopped being a concept -- which is
    #: what an external tool rewriting a document wholesale does, dropping the
    #: frontmatter and with it the `id`. Telling those apart needs the id, and
    #: `doctor` is the only thing that asks; carrying it here costs one
    #: dictionary the health pass had already built.
    missing_identities: Mapping[VaultPath, ConceptId] = field(default_factory=dict)
    broken_links: tuple[LinkRecord, ...] = ()
    unresolved_relations: tuple[UnresolvedRelation, ...] = ()
    #: Foreign notes held by path (core/01 section 1). ``new``, ``changed`` and
    #: ``missing`` name them alongside concepts: an unindexed note in the pile
    #: is as stale as an unindexed concept, and ``index`` is what clears both.
    indexed_foreign: int = 0

    @property
    def is_stale(self) -> bool:
        """Whether the vault has moved on since the last indexing run."""
        return bool(self.new or self.changed or self.missing)


class IndexService:
    def __init__(
        self,
        *,
        documents: DocumentStore,
        metadata: MetadataIndex,
        text: TextIndex,
        graph: GraphIndex,
        state: IndexState,
        clock: Clock = utc_now,
    ) -> None:
        self._documents = documents
        self._metadata = metadata
        self._text = text
        self._graph = graph
        self._state = state
        self._clock = clock

    # -- writing ----------------------------------------------------------

    def reconcile(self, *, changed_only: bool = False) -> IndexRun:
        """Bring the index in line with the vault.

        ``changed_only`` skips a document the index already has, and against a
        store that can stat its files it applies section 13's cheap ``path +
        size + mtime`` comparison the way :meth:`health` always has: the stat
        decides what to *read*, and anything it flags is read once and judged
        on its content hash. Parsing every document only to conclude that none
        had changed would make each watcher batch slow, and YAML parsing is
        most of that cost.

        The hash keeps two jobs. A store with no stats (in-memory, a future
        remote one) still reads everything and compares hashes, and the *full*
        pass -- startup, ``never4ga index``, ``rebuild`` -- still reads and
        hashes every document, so bytes that change under an unmoved stat are
        caught at the next startup rather than never. mtime is still not
        durable semantic provenance; it is only ever grounds for skipping a
        read, never for claiming a change.
        """
        indexed_at = format_timestamp(self._clock())
        if changed_only:
            stats = self._file_stats()
            if stats:
                return self._reconcile_settled(stats, indexed_at)
        issues: list[ProjectionIssue] = []
        indexed = unchanged = 0
        seen: dict[ConceptId, VaultPath] = {}

        documents = list(self._documents.iter_documents())
        identities = {document.path: document.concept_id for document in documents}
        names = _names(documents)
        stats = self._file_stats()
        to_apply: list[StoredDocument] = []
        current: dict[ConceptId, StoredDocument] = {}

        for document in documents:
            if document.concept_id in seen:
                issues.append(
                    ProjectionIssue(
                        "duplicate_id",
                        f"{document.path} claims {document.concept_id}, already used by "
                        f"{seen[document.concept_id]}; only one of them is indexed",
                    )
                )
                continue
            seen[document.concept_id] = document.path

            if changed_only and self._is_current(document):
                self._settle_stat(document, stats)
                current[document.concept_id] = document
                unchanged += 1
                continue
            to_apply.append(document)

        if changed_only:
            departed = [
                row for row in self._state.indexed_documents() if row.concept_id not in seen
            ]
            for concept_id in sorted(self._referrers_to_revisit(to_apply, departed)):
                if concept_id in current:
                    to_apply.append(current[concept_id])
                    unchanged -= 1

        for document in to_apply:
            projection = project(document)
            issues.extend(projection.issues)
            self._apply(projection, document, identities, names, indexed_at, stats, issues)
            indexed += 1

        removed = self._forget_documents_that_are_gone(seen)
        foreign = self._reconcile_foreign(
            indexed_at,
            changed_only=changed_only,
            trust_stats=False,
            concepts=seen.values(),
            directories=self._registered_on_disk(),
        )
        return IndexRun(
            indexed=indexed,
            unchanged=unchanged,
            removed=removed,
            issues=tuple(issues),
            foreign_indexed=foreign.indexed,
            foreign_unchanged=foreign.unchanged,
            foreign_removed=foreign.removed,
        )

    def _reconcile_settled(
        self, stats: Mapping[VaultPath, tuple[int, float]], indexed_at: str
    ) -> IndexRun:
        """The watcher's pass: trust the recorded stat, read what it flags.

        A recorded document whose file still carries the recorded size and
        mtime is taken as unchanged without being opened. Everything else --
        a new path, a moved file, a changed stat, a row recorded before stats
        were kept -- is read and judged exactly as the full pass judges it.

        What a changed document needs from its unread neighbours is their
        identity and their names, and both survive projection: identities come
        from the index state's rows, names from :func:`record_names` over the
        metadata projection. `tests/unit/test_projection_names.py` holds that
        reconstruction to :func:`document_names`' answer.

        Two findings only the full pass can make are consciously ceded: a
        duplicate identity between two *unchanged* files (reported when they
        were indexed, and again at every startup), and a store problem in a
        file whose stat never moved.

        A link is not one of them. Its resolution belongs to the document it
        points at as much as to the one it sits in, so an unchanged document
        whose link a change would resolve differently is read and reindexed
        with the changes (:meth:`_referrers_to_revisit`).
        """
        issues: list[ProjectionIssue] = []
        indexed = 0

        recorded = tuple(self._state.indexed_documents())
        trusted = {
            row.concept_id: row.path
            for row in recorded
            if stats.get(row.path) == (row.file_size, row.modified_at)
        }
        settled_paths = set(trusted.values())
        unchanged = len(trusted)
        # A foreign note with frontmatter of its own is concept-eligible by
        # path, so it is in `stats` too. One whose stat has not moved is not
        # read here to learn it is not a concept.
        settled_paths.update(
            record.path
            for record in self._state.indexed_paths()
            if stats.get(record.path) == (record.file_size, record.modified_at)
        )

        seen: dict[ConceptId, VaultPath] = dict(trusted)
        to_apply: list[StoredDocument] = []
        identities = {path: concept_id for concept_id, path in trusted.items()}
        for path in sorted(path for path in stats if path not in settled_paths):
            document = self._documents.get_by_path(path)
            if document is None:
                # Not readable as a concept. The full pass reports the store's
                # problems; this one only refuses to guess.
                continue
            identities[document.path] = document.concept_id
            if document.concept_id in seen:
                issues.append(
                    ProjectionIssue(
                        "duplicate_id",
                        f"{document.path} claims {document.concept_id}, already used by "
                        f"{seen[document.concept_id]}; only one of them is indexed",
                    )
                )
                continue
            seen[document.concept_id] = document.path
            if self._is_current(document):
                self._settle_stat(document, stats)
                unchanged += 1
                continue
            to_apply.append(document)

        departed = [row for row in recorded if row.concept_id not in seen]
        applying = {document.concept_id for document in to_apply}
        for concept_id in sorted(self._referrers_to_revisit(to_apply, departed) - applying):
            referrer = self._documents.get_by_path(seen[concept_id])
            if referrer is None:
                continue
            to_apply.append(referrer)
            unchanged -= 1

        if to_apply:
            names = self._names_from_projection(
                exclude={d.concept_id for d in to_apply} | {row.concept_id for row in departed}
            )
            for document in to_apply:
                for name in document_names(document):
                    names.setdefault(name.casefold(), []).append(document.concept_id)
            resolved = {name: tuple(ids) for name, ids in names.items()}
            for document in to_apply:
                projection = project(document)
                issues.extend(projection.issues)
                self._apply(projection, document, identities, resolved, indexed_at, stats, issues)
                indexed += 1

        removed = self._forget_documents_that_are_gone(seen)
        foreign = self._reconcile_foreign(
            indexed_at,
            changed_only=True,
            trust_stats=True,
            concepts=seen.values(),
            directories=self._registered_in_projection(_manifest_in(seen)),
        )
        return IndexRun(
            indexed=indexed,
            unchanged=unchanged,
            removed=removed,
            issues=tuple(issues),
            foreign_indexed=foreign.indexed,
            foreign_unchanged=foreign.unchanged,
            foreign_removed=foreign.removed,
        )

    def _reconcile_foreign(
        self,
        indexed_at: str,
        *,
        changed_only: bool,
        trust_stats: bool,
        concepts: Collection[VaultPath],
        directories: frozenset[str],
    ) -> _ForeignRun:
        """Bring the text index in line with the registered foreign material.

        Foreign material is chunked and indexed by path, for search only (core/01
        section 1). The same three rules as a concept's pass, applied to a path record:
        the stat decides what to read when the pass may trust it (``trust_stats``), the
        content hash decides what to reindex when only changes are wanted, and a
        recorded path the walk no longer produces -- deleted, renamed, adopted, or in a
        directory no longer registered -- is forgotten along with its chunks.

        ``concepts`` are the paths this pass holds as concepts. A concept
        inside foreign material is the concept it is, and is never read again
        here to be told so.
        """
        recorded = {record.path: record for record in self._state.indexed_paths()}
        store = self._documents
        notes: list[ForeignNote] = []
        stats: dict[VaultPath, tuple[int, float]] = {}
        seen: set[VaultPath] = set()
        unchanged = 0
        if isinstance(store, ForeignMaterialStore):
            stats = self._foreign_stats(directories)
            if trust_stats and stats:
                held = set(concepts)
                for path in sorted(stats):
                    row = recorded.get(path)
                    if row is not None and (row.file_size, row.modified_at) == stats[path]:
                        seen.add(path)
                        unchanged += 1
                        continue
                    if path in held:
                        continue
                    note = store.get_foreign_note(path)
                    if note is not None:
                        notes.append(note)
            else:
                notes = list(store.iter_foreign_notes(directories))

        indexed = 0
        for note in notes:
            seen.add(note.path)
            size, modified_at = stats.get(note.path) or (None, None)
            row = recorded.get(note.path)
            if changed_only and row is not None and row.content_hash == note.content_hash:
                if (row.file_size, row.modified_at) != (size, modified_at):
                    # Read, and found unchanged: settle the stat so the next
                    # cheap comparison agrees, as `_settle_stat` does.
                    self._state.record_path(replace(row, file_size=size, modified_at=modified_at))
                unchanged += 1
                continue
            # Content first, bookkeeping last, as `_apply` orders it.
            self._text.remove_path(note.path)
            for chunk in project_foreign(note):
                self._text.index_chunk(chunk)
            self._state.record_path(
                IndexedPath(
                    path=note.path,
                    content_hash=note.content_hash,
                    indexed_at=indexed_at,
                    file_size=size,
                    modified_at=modified_at,
                )
            )
            indexed += 1

        removed = 0
        for path in recorded:
            if path in seen:
                continue
            self._text.remove_path(path)
            self._state.forget_path(path)
            removed += 1
        return _ForeignRun(indexed=indexed, unchanged=unchanged, removed=removed)

    def _registered_on_disk(self) -> frozenset[str]:
        """What the system manifest registers as foreign material (core/01 section 1).

        Read from the Markdown, which is what a full pass does with everything.
        """
        document = self._documents.get_by_path(SYSTEM_MANIFEST)
        if document is None:
            return frozenset()
        return registered_foreign_material(document.frontmatter)

    def _registered_in_projection(self, manifest: ConceptId | None) -> frozenset[str]:
        """The same registration, read back from the metadata projection.

        For the settled pass and :meth:`health`, which must not open a document
        whose stat never moved -- that is the cost the settled pass exists to
        avoid, and `health` runs before every read command. The projection
        carries the field whole, as it carries every unmodelled one. A manifest
        that did change was reindexed by the concept half of the pass before
        this runs, so the projection is never behind it; one the index does
        not hold registers nothing until the full pass that indexes it.
        """
        if manifest is None:
            return frozenset()
        record = self._metadata.get(manifest)
        if record is None:
            return frozenset()
        return registered_foreign_material(record.extra)

    def _foreign_stats(self, directories: Collection[str]) -> dict[VaultPath, tuple[int, float]]:
        if not isinstance(self._documents, FileStatReportingStore):
            return {}
        return {
            stat.path: (stat.size, stat.modified_at)
            for stat in self._documents.iter_foreign_file_stats(directories)
        }

    def _referrers_to_revisit(
        self, changed: Sequence[StoredDocument], departed: Sequence[IndexedDocument]
    ) -> set[ConceptId]:
        """Documents whose links would resolve differently after this change.

        A link is resolved when the document holding it is indexed, against the
        vault as it is then. A changed-only pass that read only what changed
        would leave every other document's links as they were: a target that
        arrived after its referrer would stay a `broken_link` -- and a missing
        graph edge -- until the next full pass, which may be days away.

        Must run before any of ``changed`` is applied, while the index still
        says where each document was and what it answered to. A link needs
        revisiting when a path or name it addresses was *lost* -- its document
        left, moved, or stopped answering to the name -- or was *gained* by a
        document other than the one it resolved to, which is also how a second
        namesake makes a link ambiguous. An edit that keeps a document's path
        and names changes no link, so the page everything links to can be
        edited without the pass reading everything that links to it.
        """
        if not changed and not departed:
            return set()
        gained_paths = {document.path: document.concept_id for document in changed}
        gained_names: dict[str, set[ConceptId]] = {}
        lost_paths: set[VaultPath] = {row.path for row in departed}
        lost_names: set[str] = set()
        for row in departed:
            lost_names |= self._recorded_names(row.concept_id)
        for document in changed:
            now = {name.casefold() for name in document_names(document)}
            for name in now:
                gained_names.setdefault(name, set()).add(document.concept_id)
            before = self._state.get(document.concept_id)
            if before is not None and before.path != document.path:
                lost_paths.add(before.path)
            lost_names |= self._recorded_names(document.concept_id) - now

        def moved(link: LinkRecord) -> bool:
            if link.target_path is not None:
                return (
                    link.target_path in lost_paths
                    or gained_paths.get(link.target_path, link.target_id) != link.target_id
                )
            name = (link.target_name or "").casefold()
            return name in lost_names or bool(gained_names.get(name, set()) - {link.target_id})

        return {link.source for link in self._state.links() if moved(link)}

    def _recorded_names(self, concept_id: ConceptId) -> set[str]:
        record = self._metadata.get(concept_id)
        return set() if record is None else {name.casefold() for name in record_names(record)}

    def _names_from_projection(self, exclude: set[ConceptId]) -> dict[str, list[ConceptId]]:
        """What every unread document is called, without reading one."""
        found: dict[str, list[ConceptId]] = {}
        for record in self._metadata.query(MetadataQuery()):
            if record.concept_id in exclude:
                continue
            for name in record_names(record):
                found.setdefault(name.casefold(), []).append(record.concept_id)
        return found

    def _chunks(self) -> list[IndexedChunk]:
        """Every chunk the vault currently produces.

        Recomputed from Markdown rather than read back from the index: the
        canonical store is the only thing that is authoritative about what a
        chunk *is*, and a backlog measured against a stale projection would
        embed the wrong text.
        """
        return [
            chunk
            for document in self._documents.iter_documents()
            for chunk in build_chunks(document)
        ]

    def rebuild(self) -> IndexRun:
        """Discard every projection and build them again from Markdown.

        The invariant of section 17, made a command: nothing durable lives only
        in the index, so throwing it away costs time and nothing else.
        """
        self._text.clear()
        self._graph.clear()
        self._state.clear()
        self._metadata.clear()
        return self.reconcile()

    # -- reading ----------------------------------------------------------

    def health(self) -> IndexHealth:
        """What the index knows, without changing any of it."""
        recorded = {indexed.concept_id: indexed for indexed in self._state.indexed_documents()}
        concept_paths = {indexed.path: indexed for indexed in recorded.values()}
        foreign_paths = {record.path: record for record in self._state.indexed_paths()}
        by_path: dict[VaultPath, IndexedDocument | IndexedPath] = {
            **foreign_paths,
            **concept_paths,
        }

        new: list[VaultPath] = []
        suspect: list[VaultPath] = []
        present: set[VaultPath] = set()
        manifest = concept_paths.get(SYSTEM_MANIFEST)
        current_state = self._current_state(manifest.concept_id if manifest is not None else None)
        for path, current in current_state.items():
            present.add(path)
            indexed = by_path.get(path)
            if indexed is None:
                new.append(path)
            elif not _matches(indexed, current):
                suspect.append(path)
        changed = self._really_changed(suspect, concept_paths, foreign_paths)

        return IndexHealth(
            indexed_documents=len(recorded),
            last_indexed_at=max((indexed.indexed_at for indexed in by_path.values()), default=None),
            new=tuple(sorted(new)),
            changed=tuple(sorted(changed)),
            missing=tuple(sorted(path for path in by_path if path not in present)),
            missing_identities={
                path: indexed.concept_id
                for path, indexed in concept_paths.items()
                if path not in present
            },
            broken_links=tuple(link for link in self._state.links() if link.is_broken),
            unresolved_relations=self._unresolved_relations(set(recorded)),
            indexed_foreign=len(foreign_paths),
        )

    # -- internals --------------------------------------------------------

    def _settle_stat(
        self, document: StoredDocument, stats: Mapping[VaultPath, tuple[int, float]]
    ) -> None:
        """Record the stat of a document whose content turned out unchanged.

        The pass has just read the file and found the hash it already had, so
        the cheap comparison in :meth:`health` is the only thing still
        disagreeing. Recording what was measured settles it, and keeps the
        answer on every read cheap. The hash and the original ``indexed_at``
        are carried over untouched: nothing was reindexed, so claiming it was
        would be a lie about provenance.
        """
        indexed = self._state.get(document.concept_id)
        if indexed is None:
            return
        size, modified_at = stats.get(document.path) or (None, None)
        if (indexed.file_size, indexed.modified_at) == (size, modified_at):
            return
        self._state.record(replace(indexed, file_size=size, modified_at=modified_at))

    def _really_changed(
        self,
        suspect: list[VaultPath],
        by_path: Mapping[VaultPath, IndexedDocument],
        foreign: Mapping[VaultPath, IndexedPath],
    ) -> list[VaultPath]:
        """Confirm a cheap mismatch against the content hash.

        details/data-indexing-maintenance.md section 13: ``path + size + mtime``
        "may avoid unnecessary reads", but change detection is content-hash
        based and mtime is not durable semantic provenance. So the stat answers
        the common case -- nothing touched, nothing read -- and anything it
        flags is read once and judged on its hash.

        Without this, a file whose mtime moved but whose bytes did not is
        reported as changed forever: an incremental pass compares hashes, so it
        correctly declines to reindex, and the recorded stat is never refreshed.
        `git checkout`, a pull, rsync and a formatter all do exactly that.
        """
        if not suspect:
            return []
        wanted = {path for path in suspect if path in by_path}
        confirmed = (
            [
                document.path
                for document in self._documents.iter_documents()
                if document.path in wanted
                and (indexed := by_path.get(document.path)) is not None
                and indexed.content_hash != document.content_hash
            ]
            if wanted
            else []
        )
        # A foreign note is read one at a time: there is no identity to walk
        # the vault for, and a note that stopped being foreign -- it gained
        # an `id` -- has changed as far as the index is concerned.
        for path in suspect:
            record = foreign.get(path)
            if record is None or path in by_path:
                continue
            note = (
                self._documents.get_foreign_note(path)
                if isinstance(self._documents, ForeignMaterialStore)
                else None
            )
            if note is None or note.content_hash != record.content_hash:
                confirmed.append(path)
        return confirmed

    def _apply(
        self,
        projection: DocumentProjection,
        document: StoredDocument,
        identities: Mapping[VaultPath, ConceptId],
        names: Mapping[str, tuple[ConceptId, ...]],
        indexed_at: str,
        stats: Mapping[VaultPath, tuple[int, float]],
        issues: list[ProjectionIssue],
    ) -> None:
        """Write one document into every projection.

        The order is deliberate: content first, bookkeeping last. A run
        interrupted halfway leaves a document that is searchable but recorded as
        unindexed, so the next pass redoes it. The opposite order would leave it
        recorded as done and invisible.
        """
        concept_id = document.concept_id
        self._metadata.upsert(projection.record)

        self._text.remove_document(concept_id)
        for chunk in projection.chunks:
            self._text.index_chunk(chunk)

        links = _link_records(projection, identities, names, issues)
        self._graph.replace_outgoing_edges(
            concept_id,
            (
                *projection.edges,
                *_link_edges(concept_id, links),
                *_lineage_edges(projection.record),
            ),
        )
        self._state.replace_links(concept_id, links)

        size, modified_at = stats.get(document.path) or (None, None)
        self._state.record(
            IndexedDocument(
                concept_id=concept_id,
                path=document.path,
                content_hash=projection.content_hash,
                indexed_at=indexed_at,
                file_size=size,
                modified_at=modified_at,
            )
        )

    def _forget_documents_that_are_gone(self, seen: Mapping[ConceptId, VaultPath]) -> int:
        removed = 0
        for indexed in self._state.indexed_documents():
            if indexed.concept_id in seen:
                continue
            self._metadata.remove(indexed.concept_id)
            self._text.remove_document(indexed.concept_id)
            self._graph.remove_document(indexed.concept_id)
            self._state.forget(indexed.concept_id)
            removed += 1
        return removed

    def _is_current(self, document: StoredDocument) -> bool:
        indexed = self._state.get(document.concept_id)
        return (
            indexed is not None
            and indexed.content_hash == document.content_hash
            and indexed.path == document.path
        )

    def _file_stats(self) -> dict[VaultPath, tuple[int, float]]:
        if not isinstance(self._documents, FileStatReportingStore):
            return {}
        return {
            stat.path: (stat.size, stat.modified_at) for stat in self._documents.iter_file_stats()
        }

    def _current_state(
        self, manifest: ConceptId | None
    ) -> dict[VaultPath, tuple[int, float] | str]:
        """Each document in the vault, described as cheaply as the store allows.

        A store that can stat its files answers without parsing them (section
        13). One that cannot -- an in-memory store, a future remote one -- falls
        back to the content hash, which is more work and never wrong.
        """
        stats = self._file_stats()
        store = self._documents
        directories = (
            self._registered_in_projection(manifest)
            if isinstance(store, ForeignMaterialStore)
            else frozenset()
        )
        if stats:
            return {**self._foreign_stats(directories), **stats}
        current: dict[VaultPath, tuple[int, float] | str] = {
            document.path: document.content_hash for document in store.iter_documents()
        }
        if isinstance(store, ForeignMaterialStore):
            for note in store.iter_foreign_notes(directories):
                current.setdefault(note.path, note.content_hash)
        return current

    def _unresolved_relations(self, known: set[ConceptId]) -> tuple[UnresolvedRelation, ...]:
        """Typed relations pointing at nothing (section 21).

        Asked of the graph one indexed document at a time rather than in one
        query, so it stays a port call: a Neo4j projection answers it the same
        way a SQLite one does.
        """
        unresolved: list[UnresolvedRelation] = []
        for concept_id in sorted(known):
            for neighbor in self._graph.neighbors(concept_id, direction=Direction.OUTGOING):
                if neighbor.concept_id not in known:
                    unresolved.append(
                        UnresolvedRelation(
                            source=concept_id,
                            relation_type=neighbor.relation_type,
                            target=neighbor.concept_id,
                        )
                    )
        return tuple(unresolved)


def _names(documents: Sequence[StoredDocument]) -> dict[str, tuple[ConceptId, ...]]:
    """Every name a bare wikilink could address, to the documents answering to it.

    Case-insensitive, because Obsidian is. A name reaching more than one
    document is kept as more than one: picking a winner is what Obsidian's
    shortest-path heuristic does, and a heuristic has no place in mechanical
    acquisition (core/07).
    """
    found: dict[str, list[ConceptId]] = {}
    for document in documents:
        for name in document_names(document):
            found.setdefault(name.casefold(), []).append(document.concept_id)
    return {name: tuple(ids) for name, ids in found.items()}


def _link_edges(concept_id: ConceptId, links: Sequence[LinkRecord]) -> list[GraphEdge]:
    """Every link that resolved to a concept, as an edge.

    A link that resolved to nothing is deliberately not one. It is still a
    record -- that record *is* the broken-link finding -- but a relationship to
    a document that does not exist is not something retrieval can traverse.

    Nor is a link to the document it sits in. A bare ``[#anchor]`` addresses the
    current document, and a self-edge would let anything with a table of
    contents corroborate itself once the graph lane votes rather than only
    expands.
    """
    return [
        GraphEdge(source=concept_id, target=link.target_id, relation_type=LINK_RELATION)
        for link in links
        if link.target_id is not None and link.target_id != concept_id
    ]


def _lineage_edges(record: MetadataRecord) -> list[GraphEdge]:
    """A workspace's parent.

    Membership -- the `workspace` field every concept carries -- is deliberately
    absent. That is Stage B's structural reduction and is already how the
    candidate set is bounded; making it an edge as well would put every document
    in a workspace one hop from every other, and the relational lane would
    return the workspace from any starting point.
    """
    if record.concept_type != "workspace":
        return []
    parent = record.extra.get("parent")
    if not isinstance(parent, str):
        return []
    try:
        target = ConceptId.parse(parent)
    except IdentityError:
        # `validate` and `doctor` report a malformed reference. An indexer that
        # raised here would make one typo cost the whole run.
        return []
    return [GraphEdge(source=record.concept_id, target=target, relation_type=PARENT_RELATION)]


def _link_records(
    projection: DocumentProjection,
    identities: Mapping[VaultPath, ConceptId],
    names: Mapping[str, tuple[ConceptId, ...]],
    issues: list[ProjectionIssue],
) -> list[LinkRecord]:
    """Resolve each vault-internal link to an identity, where one exists.

    Resolution needs the whole vault, which is why it happens here rather than
    in the projection. A link that resolves to nothing is still kept: that record
    *is* the broken-link finding (section 21).

    Reserved navigation is the exception. core/01 section 6 makes ``index.md``
    OKF navigation, so it never carries concept frontmatter and never becomes a
    concept -- which means a link to one can never resolve to an identity, and
    recording it would report every navigation link in a spec-conforming vault
    as broken. A link to the *directory* is skipped for the same reason.

    The links between concepts are what this graph is for. Whether a reserved
    index exists is a separate question, and `doctor` asks it separately.
    """
    records: list[LinkRecord] = []
    for link in projection.links:
        if link.is_external:
            continue
        if link.target_path is not None and link.target_path.name == RESERVED_INDEX:
            continue
        if link.target_path is not None:
            records.append(
                LinkRecord(
                    source=projection.record.concept_id,
                    target_path=link.target_path,
                    target_id=identities.get(link.target_path),
                    anchor=link.anchor,
                )
            )
        elif link.target_name:
            records.append(
                LinkRecord(
                    source=projection.record.concept_id,
                    target_name=link.target_name,
                    target_id=_by_name(link.target_name, names, projection, issues),
                    anchor=link.anchor,
                )
            )
    return records


def _by_name(
    name: str,
    names: Mapping[str, tuple[ConceptId, ...]],
    projection: DocumentProjection,
    issues: list[ProjectionIssue],
) -> ConceptId | None:
    """The one document this name addresses, or nothing and a reason."""
    candidates = names.get(name.casefold(), ())
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        issues.append(
            ProjectionIssue(
                "ambiguous_link",
                f"{projection.record.path}: [[{name}]] matches {len(candidates)} documents; "
                "rename one, or write the link as a path",
            )
        )
    return None


def _manifest_in(seen: Mapping[ConceptId, VaultPath]) -> ConceptId | None:
    """The identity a pass holds at the system manifest's path, if any."""
    return next((concept_id for concept_id, path in seen.items() if path == SYSTEM_MANIFEST), None)


@dataclass(frozen=True, slots=True)
class _ForeignRun:
    indexed: int = 0
    unchanged: int = 0
    removed: int = 0


def _matches(indexed: IndexedDocument | IndexedPath, current: tuple[int, float] | str) -> bool:
    """Whether what the vault holds is what the index recorded."""
    if isinstance(current, str):
        return indexed.content_hash == current
    size, modified_at = current
    return indexed.file_size == size and indexed.modified_at == modified_at
