"""Wiring shared by the composition roots.

`core/05` section 15: the HTTP API, the CLI and MCP call the same application
services. Assemblies written by hand in each interface drift apart. There are
three roots, `cli`, `service` and `mcp.toolbox`, each picking its own adapters
and none able to import another, so anything they must assemble identically
lives here, where all of them can see it.

`tests/integration/test_composition_parity.py` checks that the three roots
agree. `test_layering.py` cannot catch a root that assembles less than its
siblings, because the layering is still correct.

It is a root's layer, not a service's: it may see `adapters` and `services` at
once, which no module below it may do. Nothing else may import it, and nothing
here may be imported by a service.

It assembles the work-management providers, joining a workspace's declared
connection to an adapter, its token to the secret store and its cache to a
file, and the adapter service, which `doctor` needs in every root for its
"Agent adapters" report (`details/data-indexing-maintenance.md` section 24).

Two rules shape it.

**Nothing is created merely by looking.** A vault with no tracker must not end
up with an empty ``trackers.sqlite3`` because somebody ran `context startup`, so
the database opens on the first read that actually needs it and not before.
`platform_paths` says the same thing about every other derived path: asking
where something belongs must not create it.

**The token stops here.** It is read from the machine-local secret store, handed
to the adapter, and never returned, logged or written anywhere else
(`core/03` section 16, `details/security-configuration.md` section 4).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from never4ga.adapters.filesystem import (
    DeploymentFile,
    FileSystemClientFileStore,
    FileSystemMarkdownStore,
    GitRepositoryLocator,
    LocalSecretFileStore,
    VaultWorkspaceMappings,
)
from never4ga.adapters.null import NullWorkspaceMappingStore
from never4ga.adapters.sqlite.session_store import SQLiteSessionStore
from never4ga.adapters.sqlite.sessions import open_sessions
from never4ga.adapters.sqlite.tracker_cache import SQLiteTrackerCache
from never4ga.adapters.sqlite.trackers import open_trackers
from never4ga.adapters.work_management import provider_for, writer_for
from never4ga.build import STARTUP_BUILD_ID, build_matches
from never4ga.client_descriptors import load_descriptors
from never4ga.config import LocalConfig
from never4ga.domain.connections import Connection
from never4ga.domain.identity import ConceptId, SessionId, WorkItemKey
from never4ga.domain.sessions import (
    Checkpoint,
    GivenContext,
    Session,
    SessionStateError,
    WorkAction,
)
from never4ga.errors import SessionStoreError
from never4ga.layout import INTEGRATIONS_DIRECTORY, SYSTEM_MANIFEST
from never4ga.platform_paths import PlatformPaths
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.secret_store import SecretStore
from never4ga.ports.tracker_cache import CachedWorkItem
from never4ga.ports.vault_files import VaultFileStore
from never4ga.ports.work_management import WorkManagementProvider, WorkManagementWriter
from never4ga.ports.workspace_mappings import WorkspaceMappingStore
from never4ga.service_client import ServiceClient, probe
from never4ga.services import LOCAL_API_CREDENTIAL
from never4ga.services.adapters import AdapterService
from never4ga.services.connections import ConnectionRegistry, secret_ref_for
from never4ga.services.trackers import CachingWorkManagementProvider
from never4ga.services.work_reading import ProviderFactory, WorkReadService
from never4ga.services.work_signals import WorkManagementSignalProvider
from never4ga.services.work_writing import WorkWriteService, WriterFactory
from never4ga.services.workspaces import WorkspaceService, vault_holds

__all__ = [
    "FileSessionStore",
    "FileTrackerCache",
    "provider_factory",
    "service_client",
    "work_read_service",
    "work_signal_provider",
    "work_write_service",
    "writer_factory",
]


class FileTrackerCache:
    """A tracker cache that opens its database for each operation.

    This solves two problems. **Nothing is created merely by looking**: a
    vault with no tracker must not acquire a ``trackers.sqlite3`` because
    somebody ran `context startup`, so the file appears on the first operation
    that needs it. And **no lifetime has to be plumbed**: the API opens a
    session per request and the CLI a database per command, and a cache that
    held a connection across either would need each root to close it.

    Opening SQLite is cheap and the operations here are few: a search caches
    once, a ticket read reads once. `services/session.py` makes the same trade
    for the index, where a session is deliberately short-lived.
    """

    def __init__(self, database: Path) -> None:
        self._database = database

    @contextmanager
    def _open(self) -> Iterator[SQLiteTrackerCache]:
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with closing(open_trackers(self._database)) as connection:
            yield SQLiteTrackerCache(connection)

    def get(self, key: WorkItemKey) -> CachedWorkItem | None:
        if not self._database.exists():
            # Reading from a cache that does not exist is a miss, not a reason
            # to bring one into being.
            return None
        with self._open() as cache:
            return cache.get(key)

    def put(self, entry: CachedWorkItem) -> None:
        with self._open() as cache:
            cache.put(entry)

    def put_many(self, entries: Sequence[CachedWorkItem]) -> None:
        if not entries:
            return
        with self._open() as cache:
            cache.put_many(entries)

    def entries(self, connection: str, project_ref: str) -> Sequence[CachedWorkItem]:
        if not self._database.exists():
            return ()
        with self._open() as cache:
            return cache.entries(connection, project_ref)

    def forget(self, key: WorkItemKey) -> None:
        if not self._database.exists():
            return
        with self._open() as cache:
            cache.forget(key)

    def clear(self) -> None:
        if not self._database.exists():
            return
        with self._open() as cache:
            cache.clear()


class FileSessionStore:
    """A session store that opens its database for each operation.

    The same trade :class:`FileTrackerCache` makes, for the same two reasons:
    no lifetime has to be plumbed through two composition roots, and nothing is
    created merely by looking.

    The second reason lands differently here. A tracker cache that does not
    exist means a miss; a session store that does not exist means the session
    was never opened, which is exactly what a caller needs to be told. So reads
    against an absent file answer "not found" rather than creating one, and
    only `open` -- the operation that has something to record -- brings the
    file into being.
    """

    def __init__(self, database: Path) -> None:
        self._database = database

    @contextmanager
    def _open(self) -> Iterator[SQLiteSessionStore]:
        """Open for one operation, translating what opening can fail on.

        :class:`SQLiteSessionStore` translates the statements and
        :func:`open_sessions` translates connecting and migrating. What is
        left is the directory: this store creates it on the way in, so a
        read-only or full parent fails here, and a caller of the port should
        not have to catch ``OSError`` to find out.
        """
        try:
            self._database.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SessionStoreError(
                f"the session store at {self._database} has no directory to live in: {error}"
            ) from error
        connection = open_sessions(self._database)
        with closing(connection):
            yield SQLiteSessionStore(connection)

    def open(self, session: Session) -> None:
        with self._open() as store:
            store.open(session)

    def get(self, session: SessionId) -> Session | None:
        if not self._database.exists():
            return None
        with self._open() as store:
            return store.get(session)

    def append(self, checkpoint: Checkpoint) -> None:
        if not self._database.exists():
            raise SessionStateError(
                f"session {checkpoint.session} was never opened; "
                "a checkpoint that belongs to nothing cannot be wrapped"
            )
        with self._open() as store:
            store.append(checkpoint)

    def record_work_action(self, action: WorkAction) -> None:
        if not self._database.exists():
            raise SessionStateError(
                f"session {action.session} was never opened; "
                "an action belonging to nothing can never be reconciled"
            )
        with self._open() as store:
            store.record_work_action(action)

    def work_actions(self, session: SessionId) -> Sequence[WorkAction]:
        if not self._database.exists():
            return ()
        with self._open() as store:
            return store.work_actions(session)

    def record_given_context(self, given: Sequence[GivenContext]) -> None:
        if not given:
            return
        if not self._database.exists():
            raise SessionStateError(
                f"session {given[0].session} was never opened; "
                "what it was given cannot be recorded against nothing"
            )
        with self._open() as store:
            store.record_given_context(given)

    def given_context(self, session: SessionId) -> Sequence[GivenContext]:
        if not self._database.exists():
            return ()
        with self._open() as store:
            return store.given_context(session)

    def mark_wrapped(self, session: SessionId, log: ConceptId) -> None:
        if not self._database.exists():
            raise SessionStateError(f"session {session} was never opened")
        with self._open() as store:
            store.mark_wrapped(session, log)

    def checkpoints(self, session: SessionId) -> Sequence[Checkpoint]:
        if not self._database.exists():
            return ()
        with self._open() as store:
            return store.checkpoints(session)

    def recent(
        self, workspace: ConceptId, *, limit: int = 10, before: datetime | None = None
    ) -> Sequence[Session]:
        if not self._database.exists():
            return ()
        with self._open() as store:
            return store.recent(workspace, limit=limit, before=before)


def work_signal_provider(
    *,
    documents: DocumentStore,
    secrets: SecretStore,
    trackers_database: Path,
) -> WorkManagementSignalProvider:
    """The Stage F provider for whatever tracker this vault's workspaces declare.

    Always returns one. Whether it says anything is the provider's decision and
    depends on the workspace a request resolves to, which is not knowable here.
    """
    cache = FileTrackerCache(trackers_database)

    def factory(connection: Connection) -> WorkManagementProvider | None:
        provider = provider_for(connection, token=secrets.get(secret_ref_for(connection)))
        if provider is None:
            return None
        # Wrapped here rather than in the adapter registry: caching is a
        # service, services sit beside adapters rather than beneath them, and
        # the composition root is what may see both.
        return CachingWorkManagementProvider(
            provider,
            cache=cache,
            connection=connection.name,
            project_ref=connection.project_ref or "",
        )

    return WorkManagementSignalProvider(
        documents=documents,
        connections=ConnectionRegistry(documents=documents),
        factory=factory,
        cache=cache,
    )


def writer_factory(secrets: SecretStore) -> WriterFactory:
    """Join a connection to its token and build the writable adapter.

    Here rather than in either root, for the reason `core/05` section 15 gives:
    the CLI and the daemon must agree about what a connection means, and
    agreement is cheapest when neither of them says it. `services` cannot do
    this itself -- it is written against ports, and choosing `openproject` is a
    composition root's act.
    """

    def factory(connection: Connection) -> WorkManagementWriter | None:
        return writer_for(connection, token=secrets.get(secret_ref_for(connection)))

    return factory


def work_write_service(
    documents: DocumentStore,
    workspaces: WorkspaceService,
    secrets: SecretStore,
    trackers_database: Path,
) -> WorkWriteService:
    """The write half, assembled the one way all three roots must.

    Shared here under `core/05` section 15. The cache argument below is one
    line, and a root that omitted it would keep serving pre-write answers
    while reporting every write as applied.
    """
    return WorkWriteService(
        documents=documents,
        workspaces=workspaces,
        factory=writer_factory(secrets),
        cache=FileTrackerCache(trackers_database),
    )


def work_read_service(
    documents: DocumentStore,
    workspaces: WorkspaceService,
    secrets: SecretStore,
    trackers_database: Path,
) -> WorkReadService:
    """The read half of work management, assembled the one way all roots must.

    A helper in one root is invisible to the others, and the copies they then
    make drift (`core/05` section 15). `tests/integration/
    test_composition_parity.py` checks the roots agree.
    """
    return WorkReadService(
        documents=documents,
        workspaces=workspaces,
        factory=provider_factory(secrets, trackers_database),
    )


def provider_factory(secrets: SecretStore, trackers_database: Path) -> ProviderFactory:
    """A readable adapter for a connection, cached the way reads already are.

    The read counterpart of :func:`writer_factory`, and cached where that one is
    not: `details/openproject-adapter.md` section 9 caches reads and says writes
    always operate against provider current state. Wrapping happens here rather
    than in the adapter registry because caching is a service, services sit
    beside adapters rather than beneath them, and a composition root is what may
    see both.
    """
    cache = FileTrackerCache(trackers_database)

    def factory(connection: Connection) -> WorkManagementProvider | None:
        provider = provider_for(connection, token=secrets.get(secret_ref_for(connection)))
        if provider is None:
            return None
        return CachingWorkManagementProvider(
            provider,
            cache=cache,
            connection=connection.name,
            project_ref=connection.project_ref or "",
        )

    return factory


def adapter_service(vault_root: Path, files: VaultFileStore) -> AdapterService:
    """The sync engine, assembled the one way all three roots must assemble it.

    `doctor` reports adapter drift (`details/data-indexing-maintenance.md`
    section 24), so every root needs this, not only the CLI.

    Descriptors are the shipped defaults plus whatever the vault describes in
    `50_System/Integrations/`, which is how a new client arrives without a
    release (`core/04` section 9).
    """
    return AdapterService(
        load_descriptors(vault_root / INTEGRATIONS_DIRECTORY),
        files,
        DeploymentFile(PlatformPaths.resolve().deployments_file),
        FileSystemClientFileStore(),
        home=Path.home(),
        vault_root=vault_root,
    )


def workspace_mappings(
    vault_id: ConceptId | None,
    documents: DocumentStore,
    *,
    paths: PlatformPaths | None = None,
) -> WorkspaceMappingStore:
    """This vault's repo-to-workspace mappings, and no other vault's.

    Its own file, beside its session store, plus the entries of the older
    machine-wide file whose workspace this vault holds. Those entries never
    named a vault, so the workspace is the only thing that says whose an entry
    is. A directory that is not a vault has no identity to keep mappings under.
    """
    if vault_id is None:
        return NullWorkspaceMappingStore()
    paths = paths or PlatformPaths.resolve()
    return VaultWorkspaceMappings(
        paths.workspaces_file(vault_id),
        machine=paths.machine_workspaces_file,
        claims=lambda mapping: vault_holds(documents, mapping),
    )


def workspace_service(vault_id: ConceptId | None, documents: DocumentStore) -> WorkspaceService:
    """Workspace resolution for one vault, the way all three roots must build it.

    Built once, with the vault's documents, so mappings stay per vault and a
    resolution is refused when the vault does not hold the workspace it names.
    """
    return WorkspaceService(
        workspace_mappings(vault_id, documents),
        GitRepositoryLocator(),
        documents=documents,
    )


def service_client(
    vault_root: Path,
    *,
    local: bool,
    on_stale_build: Callable[[str], None] | None = None,
) -> ServiceClient | None:
    """The running service, if one should be handed this vault's work.

    **Four conditions, shared by every root.** The user did not ask for local;
    something is listening on the configured endpoint; it is serving *this*
    vault; and it is running *this build*.

    The last two guard against the same failure, an answer that is confident
    and wrong. A machine may hold several vaults and one service, so a question
    about vault A sent to a service owning vault B is answered about the wrong
    vault. And a daemon runs the code it was started with, so after an upgrade
    a new command would reach old behaviour. MCP makes the same route decision
    per tool call and cannot import another root, so the conditions live here
    once.

    They cannot live in `service_client` itself: deciding needs config, the
    document store, the secret store and the build id at once, and that module
    may import only `errors`.

    A mismatch declines to delegate rather than refusing to run, because
    falling back in process answers correctly. `on_stale_build` is how a root that has
    somewhere to say so does; the CLI passes its reporter's note, and MCP
    passes nothing, because a tool call returns a payload rather than a
    transcript.
    """
    if local:
        return None
    endpoint = LocalConfig.load().service
    health = probe(endpoint.base_url)
    if health is None:
        return None
    manifest = FileSystemMarkdownStore(vault_root).get_by_path(SYSTEM_MANIFEST)
    if manifest is None or str(manifest.concept_id) != health.get("vault_id"):
        return None
    if not build_matches(health):
        if on_stale_build is not None:
            on_stale_build(
                f"the service at {endpoint.base_url} is running a different build "
                f"({health.get('build') or 'too old to report one'}, this is "
                f"{STARTUP_BUILD_ID}); answering in process instead. Restart it to "
                "pick up this build: `never4ga service restart`"
            )
        return None
    credential = LocalSecretFileStore(PlatformPaths.resolve().secrets_file).get(
        LOCAL_API_CREDENTIAL
    )
    return ServiceClient(endpoint.base_url, credential=credential)
