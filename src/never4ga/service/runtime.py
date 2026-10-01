"""Service startup and the reconciliation loop (core/05 sections 18 and 19).

Section 18 gives the sequence:

```text
load local config -> resolve vault -> validate system manifest -> open SQLite
-> migrate -> reconcile -> start watchers -> load adapter registry
-> start API -> schedule maintenance
```

and requires that any failure produce actionable health output. So a start
returns a report of what it did rather than a bare success, and each step says
whether it worked and why not.

Section 19 decides which failures are fatal. Without a vault directory or a
system manifest there is nothing to serve and no identity to key derived state
by, so those stop the service. A watcher that will not start costs timeliness
and nothing else, because periodic reconciliation still keeps the index
correct, so it degrades instead.

Section 18's adapter-registry step is not a startup step here, and is not
reported as one that silently succeeded.

This is a composition root: it chooses concrete adapters, as the CLI does.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from never4ga.adapters.filesystem import (
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
    GitRepositoryLocator,
    LocalSecretFileStore,
)
from never4ga.adapters.sqlite import (
    SQLiteFTS5Index,
    SQLiteGraphIndex,
    SQLiteIndexState,
    SQLiteMaintenanceFindings,
    SQLiteMetadataIndex,
    open_index,
)
from never4ga.adapters.sqlite.connection import compact as sqlite_compact
from never4ga.composition import adapter_service, workspace_mappings
from never4ga.config import LocalConfig, MaintenanceSettings, ServiceEndpoint, retired_settings
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.errors import Never4gaError, VaultPathError
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.platform_paths import PlatformPaths
from never4ga.service.watcher import Coalescer, VaultEventHandler
from never4ga.services import VaultSession, ensure_local_credential
from never4ga.services.maintenance import MaintenanceLedger, validate_changed
from never4ga.services.scheduling import MaintenanceScheduler

__all__ = [
    "Observer",
    "ReconcileLoop",
    "ServiceSettings",
    "ServiceStartupError",
    "StartupReport",
    "StartupStep",
    "VaultRuntime",
]

#: details/data-indexing-maintenance.md section 15's debounce. Long enough that
#: a multi-file save settles into one run, short enough that a note is
#: searchable about as soon as it is written.
DEFAULT_QUIET_PERIOD: Final = 0.75

#: Section 16's periodic reconciliation. It exists to recover what the watcher
#: missed, which is rare, so it is deliberately unhurried.
DEFAULT_RECONCILE_INTERVAL: Final = 300.0


class ServiceStartupError(Never4gaError):
    """The service cannot serve this vault at all."""


class Observer(Protocol):
    """The part of watchdog's Observer this module uses."""

    def schedule(self, event_handler: Any, path: str, *, recursive: bool = False) -> Any: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    """Everything the service needs that a machine can configure."""

    vault: Path
    endpoint: ServiceEndpoint = field(default_factory=ServiceEndpoint)
    quiet_period: float = DEFAULT_QUIET_PERIOD
    reconcile_interval: float = DEFAULT_RECONCILE_INTERVAL


@dataclass(frozen=True, slots=True)
class StartupStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class StartupReport:
    """What happened during startup, in the shape health output needs.

    It carries no credential. The service holds one; a report is something a
    user reads and a log records.
    """

    vault_id: ConceptId
    steps: tuple[StartupStep, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the service is serving. A degraded service is still up."""
        return True

    @property
    def degraded(self) -> bool:
        return any(not step.ok for step in self.steps)


class VaultRuntime:
    """One vault, served by one process."""

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        paths: PlatformPaths | None = None,
        observer_factory: Callable[[], Observer] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._paths = PlatformPaths.resolve() if paths is None else paths
        self._observer_factory = observer_factory
        self._clock = clock

        self._vault_id: ConceptId | None = None
        self._database: Path | None = None
        self._credential: str | None = None
        self._observer: Observer | None = None
        self._coalescer = Coalescer(quiet_period=settings.quiet_period)
        self._loop: ReconcileLoop | None = None
        self._maintenance: MaintenanceScheduler | None = None
        #: `core/05` section 10 gives the service SQLite write coordination.
        #: The watcher loop, the maintenance tiers and API requests each open
        #: their own connection, and SQLite admits one writer. The busy timeout
        #: in `adapters/sqlite/connection.py` is not sized for contention
        #: between writers, so this lock serialises them.
        #:
        #: Reentrant because a write path may open a nested session. Readers
        #: never take it: WAL lets a search run while a reconcile writes, and
        #: serialising reads would lose that for no benefit.
        self._writing = threading.RLock()
        self._maintenance_settings = MaintenanceSettings()
        self._started = False
        #: Set by `start`, cleared by the first tick that runs the full
        #: re-hash startup deferred. False before a start, so a tick on an
        #: unstarted runtime reads it rather than raising.
        self._deep_pass_pending = False

    # -- lifecycle --------------------------------------------------------

    def start(self) -> StartupReport:
        """Run section 18's sequence. Returns what each step did."""
        if self._started:
            raise ServiceStartupError("this runtime is already started")

        steps: list[StartupStep] = []
        warnings: list[str] = []

        steps.append(self._resolve_vault())
        vault_id = self._validate_manifest()
        steps.append(StartupStep("validate_manifest", True, f"vault {vault_id}"))
        self._vault_id = vault_id

        steps.append(self._open_index(vault_id))
        self._credential = self._ensure_credential(warnings)
        steps.append(self._initial_reconcile())
        self._deep_pass_pending = True
        steps.append(self._start_watcher())

        # Section 22's cadences come from the config file rather than from a
        # constant, and the schedule runs in this service rather than on a
        # system timer.
        self._maintenance_settings = LocalConfig.load().maintenance
        self._maintenance = MaintenanceScheduler(
            immediate=self._maintain_changed,
            frequent=_nothing_yet,
            periodic=self._maintain_everything,
            frequent_every=self._maintenance_settings.frequent,
            periodic_every=self._maintenance_settings.periodic,
            started_at=self._clock(),
            enabled=self._maintenance_settings.enabled,
        )
        self._loop = ReconcileLoop(
            coalescer=self._coalescer,
            reconcile=self._reconcile,
            interval=self._settings.reconcile_interval,
            started_at=self._clock(),
            after=self._after_reconcile,
        )
        self._started = True
        return StartupReport(vault_id=vault_id, steps=tuple(steps), warnings=tuple(warnings))

    def stop(self) -> None:
        """Release the watcher. Idempotent, because a stop may be a second
        signal arriving while the first is still being handled."""
        observer, self._observer = self._observer, None
        if observer is not None:
            with contextlib.suppress(Exception):
                observer.stop()
                observer.join(timeout=5.0)
        self._started = False
        # A stopped runtime has no background work outstanding: the pump that
        # would have run the deferred pass is gone with it.
        self._deep_pass_pending = False

    @property
    def running(self) -> bool:
        return self._started

    # -- what the interfaces need ----------------------------------------

    @property
    def settings(self) -> ServiceSettings:
        return self._settings

    @property
    def vault_id(self) -> ConceptId:
        if self._vault_id is None:
            raise ServiceStartupError("the runtime has not started; there is no vault identity yet")
        return self._vault_id

    @property
    def credential(self) -> str:
        if self._credential is None:
            raise ServiceStartupError("the runtime has not started; there is no credential yet")
        return self._credential

    @property
    def loop(self) -> ReconcileLoop:
        if self._loop is None:
            raise ServiceStartupError("the runtime has not started")
        return self._loop

    @contextmanager
    def sessions(self, *, write: bool = False) -> Iterator[VaultSession]:
        """One vault session, with its own connection.

        Per request, per reconciliation, per anything: WAL gives concurrent
        readers their own view, and a connection shared across threads would
        take that away.

        ``write`` declares the intent rather than detecting it. A session that
        may write holds the service's write lock for its whole life, so two of
        them never overlap; a reading session takes nothing and runs alongside.
        Declaring it is the point -- SQLite reports the collision as "database
        is locked" from whichever writer lost, which names the symptom and
        never the second writer.
        """
        vault = self._settings.vault
        # The same wiring the CLI does, from the same config (core/05 section
        # 15). Assembled here rather than shared with the CLI because the two
        # roots cannot see each other; kept identical because that is the rule.
        files = FileSystemVaultFileStore(vault)
        documents = FileSystemMarkdownStore(vault)
        with (
            self._writing if write else nullcontext(),
            closing(open_index(self._index_database())) as connection,
        ):
            yield VaultSession(
                vault_id=self.vault_id,
                files=files,
                documents=documents,
                metadata=SQLiteMetadataIndex(connection),
                text=SQLiteFTS5Index(connection),
                graph=SQLiteGraphIndex(connection),
                state=SQLiteIndexState(connection),
                repositories=GitRepositoryLocator(),
                mappings=workspace_mappings(self.vault_id, documents, paths=self._paths),
                findings=SQLiteMaintenanceFindings(connection),
                # A callable, not the result: a session is opened per request,
                # and only `doctor` ever asks.
                adapters=lambda: adapter_service(vault, files).diagnose(),
                configuration=retired_settings(),
                compact=lambda: sqlite_compact(connection),
            )

    @property
    def deep_pass_pending(self) -> bool:
        """Whether the full re-hash startup deferred has still to run."""
        return self._deep_pass_pending

    def _run_deep_pass(self) -> None:
        """The full re-hash startup deferred, once, after the socket is open.

        Failure is swallowed for the reason the reconcile loop swallows its
        own: this runs on the maintenance pump, which is expected to outlive a
        bad document. A background repair that could kill a serving process
        would be a worse trade than the latency it removes.

        The flag is cleared before the pass rather than after, so a document
        that fails every time cannot make this the only thing the pump ever
        does.
        """
        self._deep_pass_pending = False
        with contextlib.suppress(Exception):
            self._reconcile(changed_only=False)

    def tick(self, now: float | None = None) -> None:
        """Give the reconciliation loop and the maintenance tiers a chance to run."""
        moment = self._clock() if now is None else now
        self.loop.tick(now=moment)
        if self._maintenance is not None:
            self._maintenance.tick(now=moment)
        # Last, not first. Run before the loop, the deferred pass would
        # reconcile the batch the coalescer was holding, the loop would find
        # nothing changed, and the immediate maintenance tier would never see
        # the save that triggered it.
        if self._deep_pass_pending:
            self._run_deep_pass()

    @property
    def maintenance_settings(self) -> MaintenanceSettings:
        """The cadences this service read, for health output and for tests."""
        return self._maintenance_settings

    @property
    def maintenance_error(self) -> str | None:
        return None if self._maintenance is None else self._maintenance.last_error

    # -- steps ------------------------------------------------------------

    def _resolve_vault(self) -> StartupStep:
        vault = self._settings.vault
        if not vault.is_dir():
            raise ServiceStartupError(f"{vault} is not a directory; there is no vault to serve")
        return StartupStep("resolve_vault", True, str(vault))

    def _validate_manifest(self) -> ConceptId:
        """core/05 section 6: this document's id is the vault identity."""
        manifest = FileSystemMarkdownStore(self._settings.vault).get_by_path(SYSTEM_MANIFEST)
        if manifest is None:
            raise ServiceStartupError(
                f"{self._settings.vault} has no {SYSTEM_MANIFEST}, so it is not an "
                "initialized vault; run `never4ga init` first"
            )
        return manifest.concept_id

    def _open_index(self, vault_id: ConceptId) -> StartupStep:
        database = self._paths.index_database(vault_id)
        try:
            database.parent.mkdir(parents=True, exist_ok=True)
            with closing(open_index(database)):
                pass
        except (OSError, sqlite3.Error) as error:
            # Not optional. Without the index the service can answer liveness
            # and nothing else, which is worse than refusing to start.
            raise ServiceStartupError(f"cannot open the index at {database}: {error}") from error
        self._database = database
        return StartupStep("open_index", True, str(database))

    def _ensure_credential(self, warnings: list[str]) -> str:
        store = LocalSecretFileStore(self._paths.secrets_file)
        credential = ensure_local_credential(store)
        warnings.extend(store.warnings)
        return credential

    def _initial_reconcile(self) -> StartupStep:
        """Section 18: reconcile before the API answers.

        A failure here degrades. The vault is canonical and readable; only the
        projection is behind, and every read reports staleness rather than
        pretending (core/06 section 2).

        **The cheap pass, not the full one.** A full re-hash parses every
        document, and on a large vault the socket would stay closed for
        minutes while clients report the service as absent. The stat-based
        pass reaches the same index for every change a stat can see, which on
        a restart is nearly all of them.

        The full pass still runs, on the first tick once the socket is open.
        It catches bytes that changed while size and mtime did not, as a
        checkout or a sync tool can produce.
        """
        try:
            run = self._reconcile(changed_only=True)
        except Exception as error:
            return StartupStep("reconcile", False, str(error))
        return StartupStep(
            "reconcile",
            True,
            f"{run.indexed} indexed, {run.unchanged} unchanged, {run.removed} removed"
            + (
                f"; foreign notes {run.foreign_indexed} indexed, "
                f"{run.foreign_unchanged} unchanged, {run.foreign_removed} removed"
                if run.foreign_indexed or run.foreign_unchanged or run.foreign_removed
                else ""
            )
            + "; full pass scheduled",
        )

    def _start_watcher(self) -> StartupStep:
        factory = self._observer_factory or _default_observer
        try:
            observer = factory()
            observer.schedule(
                VaultEventHandler(
                    root=self._settings.vault,
                    coalescer=self._coalescer,
                    clock=self._clock,
                ),
                str(self._settings.vault),
                recursive=True,
            )
            observer.start()
        except Exception as error:
            # core/05 section 19: the index stays correct through periodic
            # reconciliation; only its timeliness is lost.
            return StartupStep(
                "start_watcher",
                False,
                f"{error}; the index will stay current through periodic reconciliation",
            )
        self._observer = observer
        return StartupStep("start_watcher", True, str(self._settings.vault))

    # -- internals --------------------------------------------------------

    def _index_database(self) -> Path:
        if self._database is None:
            return self._paths.index_database(self.vault_id)
        return self._database

    def _reconcile(self, changed_only: bool) -> Any:
        with self.sessions(write=True) as session:
            return session.indexer.reconcile(changed_only=changed_only)

    def _after_reconcile(self, batch: tuple[Path, ...]) -> None:
        if self._maintenance is not None:
            self._maintenance.after_change(batch, now=self._clock())

    # -- the tiers (services/scheduling.py says what belongs in each) ------

    def _maintain_changed(self, batch: Sequence[Path]) -> None:
        """The immediate tier: validate what the batch touched.

        Absolute paths arrive from the watcher; the vault is what makes them
        relative. Anything outside it is somebody else's file and is dropped
        rather than reported.
        """
        vault = self._settings.vault
        paths = []
        for path in batch:
            try:
                paths.append(VaultPath.parse(str(Path(path).relative_to(vault))))
            except ValueError, VaultPathError:
                continue
        if not paths:
            return
        with self.sessions(write=True) as session:
            if session.findings is None:
                return
            found = validate_changed(session.documents, session.metadata, paths)
            # No guard here: `MaintenanceScheduler._attempt` already catches
            # whatever a tier raises and remembers it for health output, which
            # is the same rule one level up. A second one would only change
            # which exception type the first one caught.
            MaintenanceLedger(session.findings).reconcile_within(found, set(paths))

    def _maintain_everything(self) -> None:
        """The periodic tier: the whole diagnosis, into the ledger.

        `VaultSession.diagnose` is the same call the API's `doctor` makes, which
        is what keeps a scheduled sweep and a person asking from producing
        different answers.
        """
        # A writing session, because `doctor` persists findings. Without the
        # write lock the ledger would swallow a locked-database failure and
        # the periodic tier would silently record nothing.
        with self.sessions(write=True) as session:
            session.diagnose()


def _nothing_yet() -> None:
    """The frequent tier, which has no rules yet.

    See `never4ga.services.scheduling`. Everything section 22 lists for this
    tier either belongs to the full diagnosis, is computed by every reconcile,
    or is not built yet. The tier exists so those rules can be added without
    changing a `config.toml` somebody has already written.
    """


class ReconcileLoop:
    """When to reconcile: after a settled burst, and on a timer.

    details/data-indexing-maintenance.md section 16 requires startup, manual,
    periodic and full-rebuild reconciliation. Startup is the runtime's; manual
    is the API's and the CLI's; this owns the other two.

    Nothing here raises. It runs on the service's own thread, and an exception
    escaping would take the daemon down because one document was malformed.
    """

    def __init__(
        self,
        *,
        coalescer: Coalescer,
        reconcile: Callable[[bool], Any],
        interval: float,
        started_at: float,
        after: Callable[[tuple[Path, ...]], Any] | None = None,
    ) -> None:
        self.coalescer = coalescer
        self._reconcile = reconcile
        self._interval = interval
        self._last_run = started_at
        self._last_error: str | None = None
        self._lock = threading.Lock()
        #: What to do with the batch once it has been indexed: section 22's
        #: immediate tier. It must run after the reconcile, so a new document's
        #: relations resolve against an index that knows about it.
        self._after = after

    @property
    def last_error(self) -> str | None:
        """The most recent failure, for health output. Cleared by a success."""
        return self._last_error

    def tick(self, *, now: float) -> bool:
        """Reconcile if anything is due. Returns whether it ran."""
        with self._lock:
            due = self.coalescer.due(now=now) or now - self._last_run >= self._interval
            if not due:
                return False
            batch = self.coalescer.take(now=now)
            self._last_run = now
        try:
            self._reconcile(True)
        except Exception as error:
            self._last_error = str(error)
            return True
        self._last_error = None
        if self._after is not None:
            # After, and only after. A tier that validated the batch before the
            # reconcile would check a new document against an index that has
            # never heard of it.
            try:
                self._after(batch)
            except Exception as error:
                self._last_error = str(error)
        return True


def _default_observer() -> Observer:
    from watchdog.observers import Observer

    return Observer()
