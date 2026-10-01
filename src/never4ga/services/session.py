"""One vault's ports, open for the length of one piece of work.

core/05 section 15: the HTTP API, the CLI and the MCP server call the same
application services. They also have to *assemble* those services the same way,
and a second assembly written by hand in a second interface is where the two
drift apart.

A session is deliberately short-lived. The API opens one per request, which
gives each request its own SQLite connection: WAL lets concurrent readers
proceed without blocking each other, and a connection shared across a
threadpool would serialise them -- or, with ``check_same_thread``, refuse them
outright.

This holds ports, never adapters. Which concrete backend fills each slot is the
composition root's decision (core/05 section 5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

from never4ga.domain.configuration import RetiredSetting
from never4ga.domain.identity import ConceptId
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.graph_index import GraphIndex
from never4ga.ports.index_state import IndexState
from never4ga.ports.maintenance_findings import MaintenanceFindings
from never4ga.ports.metadata_index import MetadataIndex
from never4ga.ports.repository_locator import RepositoryLocator
from never4ga.ports.text_index import TextIndex
from never4ga.ports.vault_files import VaultFileStore
from never4ga.ports.workspace_mappings import WorkspaceMappingStore
from never4ga.schema import ValidationLevel
from never4ga.services.adapters import AdapterFinding
from never4ga.services.doctor import Diagnosis, Doctor
from never4ga.services.indexing import IndexRun, IndexService
from never4ga.services.maintenance import MaintenanceLedger
from never4ga.services.search import SearchService

__all__ = ["AdapterDiagnosis", "SessionFactory", "VaultSession"]

#: How a session reaches the adapter diagnosis without having run it.
type AdapterDiagnosis = Callable[[], Sequence[AdapterFinding]]


@dataclass(frozen=True, slots=True)
class VaultSession:
    """The canonical store and the derived backends for one vault."""

    vault_id: ConceptId
    files: VaultFileStore
    documents: DocumentStore
    metadata: MetadataIndex
    text: TextIndex
    graph: GraphIndex
    state: IndexState
    #: How `doctor` reaches a mapped repository. Optional and
    #: optional together: the mappings say which repository belongs to which
    #: workspace, and the locator is what reads it. Absent means the repository
    #: checks do not run, which is the honest answer when a root has not wired
    #: them.
    repositories: RepositoryLocator | None = None
    mappings: WorkspaceMappingStore | None = None
    #: Where a diagnosis is remembered (`details/data-indexing-maintenance.md`
    #: section 20). Optional for the same reason the pair above is: a root
    #: with no derived database has nowhere to keep findings, and `doctor` must
    #: not bring one into existence by looking.
    findings: MaintenanceFindings | None = None
    #: What is deployed to the agent clients on this machine, and what drifted
    #: (`details/data-indexing-maintenance.md` section 24). Optional for the
    #: same reason as the rest: a root that has not wired it makes a partial
    #: diagnosis, and `Diagnosis.complete` is what stops that resolving
    #: findings nobody looked for.
    #:
    #: **A means of answering rather than an answer.** The API opens a session
    #: per request, and computing this on open would make a health poll or a
    #: search pay for a question only `doctor` asks.
    adapters: AdapterDiagnosis | None = None
    #: Machine-config keys that no longer do anything. Handed in rather
    #: than read: only a composition root may open that file, and all three
    #: must pass it or `doctor` would answer differently depending on which
    #: interface asked.
    configuration: Sequence[RetiredSetting] = ()
    #: How this session's derived store gives its disk back. Injected
    #: because compaction is backend-specific and a service is written against
    #: ports: `VACUUM` is SQLite's word, and a future PostgreSQL projection
    #: would answer the same request differently. Absent means a store with
    #: nothing to reclaim, which is the honest answer for an in-memory one.
    compact: Callable[[], None] | None = None

    @property
    def indexer(self) -> IndexService:
        return IndexService(
            documents=self.documents,
            metadata=self.metadata,
            text=self.text,
            graph=self.graph,
            state=self.state,
        )

    @property
    def searcher(self) -> SearchService:
        return SearchService(
            documents=self.documents,
            metadata=self.metadata,
            text=self.text,
            graph=self.graph,
            index=self.indexer,
        )

    def doctor(self, level: ValidationLevel = ValidationLevel.STRICT) -> Doctor:
        """A diagnosis that knows what the index knows.

        `details/data-indexing-maintenance.md` section 24.
        """
        return Doctor(
            self.files,
            self.documents,
            level=level,
            index=self.indexer.health(),
            repositories=self.repositories,
            mappings=self.mappings.load() if self.mappings is not None else (),
            adapters=None if self.adapters is None else self.adapters(),
            configuration=self.configuration,
        )

    def rebuild(self) -> IndexRun:
        """Discard every projection, build them again, and give the disk back.

        The pair belongs together for the reason :meth:`diagnose` gives: there
        are three roots to forget in, and a root that rebuilt without
        compacting would leave the file at whatever size its worst day made it,
        mostly empty space.

        Only here. `reconcile` is the common path and runs on every watcher
        batch; rewriting the whole database each time would be a worse defect
        than the one this fixes.
        """
        run = self.indexer.rebuild()
        if self.compact is not None:
            self.compact()
        return run

    def diagnose(self, level: ValidationLevel = ValidationLevel.STRICT) -> Diagnosis:
        """Run the diagnosis, and remember it.

        The pair belongs together rather than at each call site: a root that
        diagnosed without recording would leave every finding dateless, and one
        that recorded a partial diagnosis would resolve rules it never ran. Both
        mistakes are one line of forgetting, and there are three roots to forget
        in.

        Recording is skipped -- not refused -- when the run was partial or when
        this session has no store, because both are ordinary. `doctor --level
        core` is a narrower question somebody asked, and a vault with no index
        has nowhere to write.

        And a store that cannot be written loses rather than the diagnosis:
        :meth:`MaintenanceLedger.record` swallows a storage failure, because the
        answer is already computed and `core/06` section 2 does not let derived
        state cost a caller a canonical one.
        """
        diagnosis = self.doctor(level).diagnose()
        if self.findings is not None and diagnosis.complete:
            MaintenanceLedger(self.findings).record(diagnosis)
        return diagnosis


#: Opens a session and closes it again. The interface layers hold one of these
#: rather than a live connection, so nothing above the composition root decides
#: when a database is open.
SessionFactory = Callable[[], AbstractContextManager[VaultSession]]
