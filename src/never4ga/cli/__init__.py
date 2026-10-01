"""The Never4gA command line.

A thin client over :mod:`never4ga.services`. core/05 section 15 and
`details/api-cli-mcp-contract.md` section 1 are explicit that no interface owns
business logic of its own: everything here parses arguments, calls a service,
and renders the result. The HTTP API and the MCP server call the same services.

This is also a composition root, one of the places that choose concrete
adapters, which is why it may import both ``never4ga.services`` and
``never4ga.adapters``.

It runs work two ways. When a service is answering for *this* vault, the
commands that touch derived state are sent there; otherwise they run in
process. ``--local`` forces in process. The one thing that is refused rather
than done twice is an in-process write to derived state while a service owns
it: WAL makes two writers survivable, not correct.

Commands that only read canonical Markdown -- `init`, `status`, `validate` and
the creation verbs -- always run in process. The service holds nothing they
need.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Final, NamedTuple

from never4ga import rendering
from never4ga.adapters.clients import SubprocessClientRegistry
from never4ga.adapters.filesystem import (
    DeploymentFile,
    ExtensionRegistryFile,
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
    GitRepositoryLocator,
    LocalSecretFileStore,
)
from never4ga.adapters.git import GitSignalProvider
from never4ga.adapters.null import NullServiceManager
from never4ga.adapters.sqlite import (
    SQLiteFTS5Index,
    SQLiteGraphIndex,
    SQLiteIndexState,
    SQLiteMaintenanceFindings,
    SQLiteMetadataIndex,
    open_index,
)
from never4ga.adapters.sqlite.connection import compact as sqlite_compact
from never4ga.adapters.systemd import SystemdUserServiceManager
from never4ga.adapters.work_management import provider_for
from never4ga.build import STARTUP_BUILD_ID, build_matches
from never4ga.cli.connections import connection_health, connection_list, connection_set_token
from never4ga.cli.output import Format, Reporter, StructuredError, bullet_list
from never4ga.cli.work import work_read_service, work_write_service, workspace_link
from never4ga.client_descriptors import load_descriptors
from never4ga.composition import (
    FileSessionStore,
    adapter_service,
    service_client,
    work_signal_provider,
    workspace_service,
)
from never4ga.config import (
    VAULT_ENVIRONMENT_VARIABLE,
    LocalConfig,
    default_vault_root,
    retired_settings,
)
from never4ga.context.budget import deep_budget, startup_budget
from never4ga.context.inbox_signals import InboxSignalProvider
from never4ga.context.session_signals import SessionSignalProvider
from never4ga.domain.context import (
    ContextBudget,
    ContextDepth,
    ContextRequest,
    ReferenceReason,
    terms_from_task,
)
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.scope import ScopeRequest
from never4ga.domain.sessions import SessionStateError
from never4ga.domain.vendoring import Origin, OriginKind
from never4ga.errors import (
    IdentityError,
    Never4gaError,
    RepositoryMarkerError,
    ScopeResolutionError,
)
from never4ga.layout import (
    FOREIGN_MATERIAL_FIELD,
    INTEGRATIONS_DIRECTORY,
    SYSTEM_MANIFEST,
    workspace_directory_of,
)
from never4ga.platform_paths import PlatformPaths
from never4ga.ports.service_manager import ServiceManager, ServiceState, ServiceStatus
from never4ga.ports.session_store import SessionStore
from never4ga.ports.work_management import ProviderHealth
from never4ga.schema import Severity, ValidationLevel
from never4ga.service_client import (
    ServiceClient,
    ServicePresence,
    ServiceRequestError,
    ServiceTimeoutError,
    ServiceUnavailableError,
    presence,
    probe,
)
from never4ga.services import (
    CaptureError,
    CaptureService,
    ConceptCreationError,
    ConceptView,
    ContentService,
    ContextService,
    Created,
    Doctor,
    Finding,
    IndexHealth,
    IndexService,
    InitializationResult,
    SearchRequest,
    SearchService,
    VaultInitializer,
    WorkspaceService,
    foreign_note_at,
    not_a_concept,
    scope_refusal,
    validate_vault,
)
from never4ga.services.adapters import AdapterService, AdapterSyncError
from never4ga.services.authoring import OWNER_ACTOR, format_timestamp, utc_now
from never4ga.services.connections import ConnectionRegistry, secret_ref_for
from never4ga.services.dashboard import refresh as dashboard_refresh
from never4ga.services.inbox import InboxError, InboxService
from never4ga.services.maintenance import MaintenanceLedger
from never4ga.services.mcp_registration import McpRegistrationService
from never4ga.services.navigation import refresh as navigation_refresh
from never4ga.services.repair import (
    Action,
    RepairPlan,
    RepairPlanner,
    describe,
)
from never4ga.services.repository_pointers import (
    PointerAction,
    PointerPlan,
    RepositoryPointerSync,
)
from never4ga.services.sessions import SessionService
from never4ga.services.skill_authoring import SkillAuthor, SkillAuthoringError
from never4ga.services.skill_library import SkillLibrary
from never4ga.services.vendoring import VendoringService
from never4ga.services.work_recording import noting_a_lost_record, record_tracker_write
from never4ga.services.work_writing import WorkWriteService
from never4ga.services.wrap import CONTEXT_QUESTION, WorkspaceGoneError, WrapService

__all__ = ["main"]

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_USAGE: Final = 2

#: The API route each depth is served at. `focused` is reached at
#: `/v1/context/focus` because the verb reads better than the adjective; nothing
#: else about the two names differs.
_ROUTE_OF_DEPTH: Final = {
    ContextDepth.STARTUP: "startup",
    ContextDepth.FOCUSED: "focus",
    ContextDepth.DEEP: "deep",
}


@dataclass(frozen=True, slots=True)
class _Context:
    root: Path
    reporter: Reporter

    #: The running service, when one answers for this vault and ``--local``
    #: was not given. ``None`` means the command does its own work.
    client: ServiceClient | None = None

    #: Who `generated.by` names on anything this invocation writes. core/02
    #: section 5.2 makes it a MUST for an agent, and Never4gA cannot infer it:
    #: the process it runs in looks the same whoever started it.
    actor: str = OWNER_ACTOR

    @property
    def files(self) -> FileSystemVaultFileStore:
        return FileSystemVaultFileStore(self.root)

    @property
    def documents(self) -> FileSystemMarkdownStore:
        return FileSystemMarkdownStore(self.root)

    @property
    def workspaces(self) -> WorkspaceService:
        """Workspace resolution against this vault's mappings on this machine.

        Not service-backed. The mappings are machine-local durable state rather
        than derived state, and only what the service owns is routed to it, so
        these verbs stay in process. The mappings belong to this vault, and
        `composition` does the one assembly.
        """
        return workspace_service(self.vault_id, self.documents)

    @property
    def vault_id(self) -> ConceptId | None:
        """The vault's stable identity, which keys its derived state.

        core/05 section 8 addresses per-vault state by UUID rather than by path,
        so moving the vault on disk does not orphan its index.
        """
        manifest = self.documents.get_by_path(SYSTEM_MANIFEST)
        return manifest.concept_id if manifest is not None else None


@dataclass(frozen=True, slots=True)
class _Indexes:
    """The derived backends for one vault, all sharing one connection."""

    database: Path
    connection: sqlite3.Connection
    documents: FileSystemMarkdownStore
    metadata: SQLiteMetadataIndex
    text: SQLiteFTS5Index
    graph: SQLiteGraphIndex
    state: SQLiteIndexState
    findings: SQLiteMaintenanceFindings

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


class _NotIndexableError(Never4gaError):
    """The vault has no identity, so its derived state has nowhere to live."""


class _NoSuchWorkspaceError(Never4gaError):
    """`--for` named a workspace this vault has not got, or has more than one of."""


def _require_vault_id(context: _Context) -> ConceptId:
    vault_id = context.vault_id
    if vault_id is None:
        raise _NotIndexableError(
            f"{context.root} has no {SYSTEM_MANIFEST}, so it is not an initialized vault"
        )
    return vault_id


def _session_store(context: _Context) -> SessionStore | None:
    """This vault's session database, or ``None`` if this is not a vault.

    :class:`FileSessionStore` opens the file per operation, so there is no
    lifetime to plumb and nothing is created merely by looking -- the same
    trade the tracker cache makes, and the reason both roots can share one
    implementation instead of each managing a connection.
    """
    vault_id = context.vault_id
    if vault_id is None:
        return None
    return FileSessionStore(PlatformPaths.resolve().sessions_database(vault_id))


@contextmanager
def _indexes(context: _Context, *, create: bool = False) -> Iterator[_Indexes]:
    """Open this vault's derived database.

    ``create`` is false for read commands on purpose: running `doctor` or
    `search` must not bring a database into existence as a side effect. Derived
    state is something the user asks for with `index`.
    """
    vault_id = context.vault_id
    if vault_id is None:
        raise _NotIndexableError(
            f"{context.root} has no {SYSTEM_MANIFEST}, so it is not an initialized vault"
        )
    database = PlatformPaths.resolve().index_database(vault_id)
    if create:
        database.parent.mkdir(parents=True, exist_ok=True)
    documents = context.documents
    with closing(open_index(database)) as connection:
        yield _Indexes(
            database=database,
            connection=connection,
            documents=documents,
            metadata=SQLiteMetadataIndex(connection),
            text=SQLiteFTS5Index(connection),
            graph=SQLiteGraphIndex(connection),
            state=SQLiteIndexState(connection),
            findings=SQLiteMaintenanceFindings(connection),
        )


def _index_exists(context: _Context) -> bool:
    vault_id = context.vault_id
    if vault_id is None:
        return False
    return PlatformPaths.resolve().index_database(vault_id).is_file()


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command. Returns the process exit code."""
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    reporter = Reporter(Format.JSON if arguments.json else Format.HUMAN)

    root = Path(arguments.vault).expanduser()
    if not root.is_dir():
        reporter.fail(
            StructuredError(
                "vault_not_found",
                f"{root} is not a directory",
                {"vault": str(root)},
                repair_hint=(
                    "create the directory first, or point --vault / "
                    f"{VAULT_ENVIRONMENT_VARIABLE} at an existing one"
                ),
            )
        )
        return EXIT_USAGE

    actor = OWNER_ACTOR if arguments.actor is None else arguments.actor.strip()
    if not actor:
        reporter.fail(
            StructuredError(
                "invalid_actor",
                "--actor cannot be blank",
                repair_hint=(
                    "name who is writing, e.g. claude-code/claude-opus-5, or "
                    f"omit --actor to record {OWNER_ACTOR}"
                ),
            )
        )
        return EXIT_FAILED

    try:
        client = _connect(root, local=arguments.local, reporter=reporter)
    except Never4gaError as error:
        reporter.fail(
            StructuredError(
                "service_unreachable",
                str(error),
                repair_hint="check the [service] section of your config, or pass --local",
            )
        )
        return EXIT_FAILED

    handler: Callable[[_Context, argparse.Namespace], int] = arguments.handler
    try:
        return handler(_Context(root, reporter, client, actor), arguments)
    except ServiceRequestError as error:
        # The service answered, and its answer was already in section 12's
        # shape. Rendering it unchanged keeps one error vocabulary rather than
        # two (`details/api-cli-mcp-contract.md` section 12).
        reporter.fail(error.error)
        return EXIT_FAILED
    except ServiceUnavailableError as error:
        reporter.fail(
            StructuredError(
                "service_unreachable",
                str(error),
                retryable=True,
                repair_hint="pass --local to work without the service",
            )
        )
        return EXIT_FAILED
    except _ServiceOwnsTheIndexError as error:
        reporter.fail(
            StructuredError(
                "service_owns_the_index",
                str(error),
                {"vault": str(root)},
                repair_hint=(
                    "run the same command without --local and the service will do it, "
                    "or stop the service first"
                ),
            )
        )
        return EXIT_FAILED
    except _ProducerUnknownError as error:
        reporter.fail(
            StructuredError(
                "producer_unknown",
                str(error),
                repair_hint=(
                    "name yourself with --actor <producer>/<version>, e.g. "
                    "claude-code/claude-opus-5; nothing was written"
                ),
            )
        )
        return EXIT_FAILED
    except AdapterSyncError as error:
        reporter.fail(
            StructuredError(
                "adapter_sync_failed",
                str(error),
                {},
                repair_hint="check the client descriptor; nothing was written",
            )
        )
        return EXIT_FAILED
    except CaptureError as error:
        reporter.fail(
            StructuredError(
                "nothing_captured", str(error), {}, repair_hint="give it something to capture"
            )
        )
        return EXIT_FAILED
    except ConceptCreationError as error:
        # One service error, two verbs. `adopt` refuses under the code
        # `POST /v1/concepts/adopt` gives the same refusal, so the payload does
        # not depend on which surface was asked.
        adopting = arguments.handler is _adopt
        # The hint is where a *flag* belongs: this surface has one, and the
        # service's message deliberately names none.
        hint = (
            f"name it with --type {'|'.join(error.candidates)}"
            if error.candidates
            else "adjust the request; the file is untouched"
            if adopting
            else "adjust the request; nothing was written"
        )
        reporter.fail(
            StructuredError(
                "concept_not_adopted" if adopting else "concept_not_created",
                str(error),
                _issue_details(error),
                repair_hint=hint,
            )
        )
        return EXIT_FAILED
    except _NotIndexableError as error:
        reporter.fail(
            StructuredError(
                "vault_not_initialized",
                str(error),
                {"vault": str(root)},
                repair_hint="run `never4ga init` first",
            )
        )
        return EXIT_FAILED
    except Never4gaError as error:
        reporter.fail(
            StructuredError(type(error).__name__, str(error), repair_hint="see `never4ga doctor`")
        )
        return EXIT_FAILED


def _connect(root: Path, *, local: bool, reporter: Reporter | None = None) -> ServiceClient | None:
    """Find the service, if one is answering for this vault.

    The four conditions live in `composition.service_client`, so this root and
    `mcp.toolbox` cannot drift apart on them.

    All this root adds is somewhere to say so. A mismatch declines to delegate
    rather than refusing to run -- falling back in process answers correctly --
    and the note names the restart that fixes it, because silence would trade
    one invisible staleness for another.
    """
    return service_client(
        root,
        local=local,
        on_stale_build=None if reporter is None else reporter.note,
    )


def _service_manager() -> ServiceManager:
    """core/05 section 11: systemd --user first, and nothing else yet.

    The systemd adapter reports ``unsupported`` by itself on a machine without
    systemctl, so the platform check here is about not pretending a Linux
    service manager exists on macOS or Windows.
    """
    if sys.platform.startswith("linux"):
        return SystemdUserServiceManager()
    # mypy narrows sys.platform to the platform it is running on, so this line
    # looks unreachable on Linux. It is the only line that runs anywhere else.
    return NullServiceManager()  # type: ignore[unreachable]


def _issue_details(error: ConceptCreationError) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if error.candidates:
        details["candidates"] = list(error.candidates)
    if error.report is not None:
        details["issues"] = [
            {"code": issue.code, "field": issue.field, "message": issue.message}
            for issue in error.report.issues
        ]
    return details


# -- commands -------------------------------------------------------------


def _init(context: _Context, arguments: argparse.Namespace) -> int:
    documents = context.documents
    result = VaultInitializer(context.files, documents).initialize(arguments.title)
    context.reporter.emit(
        {
            "vault": str(context.root),
            "vault_id": str(result.vault_id),
            "already_initialized": result.already_initialized,
            "created": [str(path) for path in result.created],
            "preserved": [str(path) for path in result.preserved],
            "updated": [str(path) for path in result.updated],
            # What was already there and is the user's, left in place
            # (core/02 section 3.3).
            "foreign_material": [
                {"directory": one.directory, "files": one.files} for one in result.foreign_material
            ],
            "foreign_notes": list(result.foreign_notes),
            "next_steps": [
                {"command": one.command, "reason": one.reason} for one in _next_steps(result)
            ],
        },
        _init_summary(context, result),
    )
    return EXIT_OK


class _NextStep(NamedTuple):
    command: str
    reason: str


#: What to run after `init` has found somebody's notes, and why each one.
#:
#: A Context Pack is scoped to a workspace, so until one is created and mapped,
#: `search` is what reaches the notes and `context startup` refuses. That
#: refusal is deliberate, because inferring a workspace is what `workspace map`
#: exists to prevent, so the report says what to run instead.
#:
#: Nothing is printed for a vault with no existing notes: a user who created a
#: vault to fill it does not need commands about material they do not have.
#:
#: Every command here must run as written, placeholders aside. A hint that
#: does not run is worse than no hint, so
#: `test_the_next_steps_are_commands_that_exist` executes each one.
_PILE_NEXT_STEPS = (
    _NextStep("never4ga index", "then `search` reaches these notes"),
    _NextStep(
        "never4ga workspace create <Name> --type <type>",
        "a Context Pack is scoped to a workspace",
    ),
    _NextStep("never4ga workspace map <workspace-id> --repo <path>", "and a pack reaches it"),
)


def _next_steps(result: InitializationResult) -> tuple[_NextStep, ...]:
    return _PILE_NEXT_STEPS if result.foreign_material or result.foreign_notes else ()


def _init_summary(context: _Context, result: InitializationResult) -> str:
    foreign = result.foreign_material
    lines = (
        [f"Vault already initialized at {context.root}", f"  id: {result.vault_id}"]
        if not result.created
        else [
            f"Initialized vault at {context.root}",
            f"  id:        {result.vault_id}",
            f"  created:   {len(result.created)} files and directories",
            f"  preserved: {len(result.preserved)} already present",
        ]
    )
    if foreign:
        plural = "directory" if len(foreign) == 1 else "directories"
        lines.append(f"  foreign:   {len(foreign)} {plural} left in place")
        lines.extend(
            f"    {one.directory} ({one.files} {'file' if one.files == 1 else 'files'})"
            for one in foreign
        )
    if result.foreign_notes:
        count = len(result.foreign_notes)
        lines.append(
            f"  loose:     {count} loose {'note' if count == 1 else 'notes'} left in place"
        )
        lines.extend(f"    {name}" for name in result.foreign_notes)
    steps = _next_steps(result)
    if steps:
        width = max(len(one.command) for one in steps)
        lines.append("  next:")
        lines.extend(f"    {one.command:<{width}}  -- {one.reason}" for one in steps)
    return "\n".join(lines)


def _status(context: _Context, _: argparse.Namespace) -> int:
    documents = context.documents
    manifest = documents.get_by_path(SYSTEM_MANIFEST)
    concepts = list(documents.iter_documents())
    by_type: dict[str, int] = {}
    for document in concepts:
        name = str(document.frontmatter.get("type", "unknown"))
        by_type[name] = by_type.get(name, 0) + 1

    context.reporter.emit(
        {
            "vault": str(context.root),
            "initialized": manifest is not None,
            "vault_id": str(manifest.concept_id) if manifest else None,
            "title": manifest.frontmatter.get("title") if manifest else None,
            "concept_count": len(concepts),
            "concepts_by_type": dict(sorted(by_type.items())),
            "foreign_material": (
                [str(name) for name in manifest.frontmatter.get(FOREIGN_MATERIAL_FIELD) or []]
                if manifest
                else []
            ),
        },
        _status_summary(context, manifest, len(concepts), by_type),
    )
    return EXIT_OK


def _status_summary(context: _Context, manifest: Any, count: int, by_type: dict[str, int]) -> str:
    if manifest is None:
        return f"No Never4gA vault at {context.root}\n  run `never4ga init` to create one"
    lines = [
        f"{manifest.frontmatter.get('title', 'Vault')} at {context.root}",
        f"  id:       {manifest.concept_id}",
        f"  concepts: {count}",
    ]
    lines.extend(f"    {name:<18} {n}" for name, n in sorted(by_type.items()))
    return "\n".join(lines)


def _doctor(context: _Context, arguments: argparse.Namespace) -> int:
    level = ValidationLevel(arguments.level)
    if context.client is not None and not arguments.check_connections:
        payload = {"vault": str(context.root), **context.client.doctor(level.value)}
    else:
        # The probe is this machine's to make: it holds the tokens, and the
        # service has no route that takes "and reach the network while you are
        # at it". Asking for it is asking for a local diagnosis.
        payload = _local_doctor(context, level, check_connections=arguments.check_connections)
    context.reporter.emit(payload, _doctor_summary(payload))
    return EXIT_OK if payload["healthy"] else EXIT_FAILED


def _probe_connections(context: _Context) -> dict[str, ProviderHealth]:
    """Ask every configured tracker whether it answers.

    Only ever called when `--check-connections` was typed. A connection that is
    undefined, has no token, or names a provider Never4gA has no adapter for is
    reported as unavailable with the reason -- those are configuration faults a
    person should see, and they are indistinguishable from a dead server as far
    as "can this vault reach its tracker" goes.
    """
    registry = ConnectionRegistry(documents=context.documents)
    secrets = LocalSecretFileStore(PlatformPaths.resolve().secrets_file)
    health: dict[str, ProviderHealth] = {}
    for connection in registry.list_connections():
        token = secrets.get(secret_ref_for(connection))
        if token is None:
            health[connection.name] = ProviderHealth(
                available=False, detail="no token is stored on this machine"
            )
            continue
        provider = provider_for(connection, token=token)
        if provider is None:
            health[connection.name] = ProviderHealth(
                available=False,
                detail=f"no adapter for provider {connection.provider!r}",
            )
            continue
        try:
            health[connection.name] = provider.health()
        except Exception as error:  # the probe is the thing that may fail
            health[connection.name] = ProviderHealth(available=False, detail=str(error))
    return health


def _local_doctor(
    context: _Context, level: ValidationLevel, *, check_connections: bool = False
) -> dict[str, Any]:
    health = _probe_connections(context) if check_connections else None
    if not _index_exists(context):
        # No index to report on, and asking for one would create it. `doctor`
        # observes; it does not bring derived state into existence.
        diagnosis = Doctor(
            context.files,
            context.documents,
            level=level,
            repositories=GitRepositoryLocator(),
            mappings=context.workspaces.mappings(),
            adapters=_adapter_service(context).diagnose(),
            connection_health=health,
            configuration=retired_settings(),
        ).diagnose()
    else:
        with _indexes(context) as indexes:
            diagnosis = Doctor(
                context.files,
                context.documents,
                level=level,
                index=indexes.indexer.health(),
                repositories=GitRepositoryLocator(),
                mappings=context.workspaces.mappings(),
                adapters=_adapter_service(context).diagnose(),
                connection_health=health,
                configuration=retired_settings(),
            ).diagnose()
            # `details/data-indexing-maintenance.md` section 20: the run is
            # what gives a finding its `detected_at`, so it is recorded here
            # rather than by whoever reads the payload. A partial run records
            # nothing -- see `Diagnosis.complete`.
            if diagnosis.complete:
                MaintenanceLedger(indexes.findings).record(diagnosis)
    return {
        "vault": str(context.root),
        "vault_id": str(diagnosis.vault_id) if diagnosis.vault_id else None,
        "healthy": diagnosis.healthy,
        "concept_count": diagnosis.concept_count,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity.value,
                "message": finding.message,
                "path": str(finding.path) if finding.path else None,
                "repair_hint": finding.repair_hint,
            }
            for finding in diagnosis.findings
        ],
    }


def _doctor_summary(payload: dict[str, Any]) -> str:
    """Human output is a rendering of the JSON, never a second source.

    Both modes reach this with the same payload, which is what keeps a
    service-backed `doctor` and an in-process one saying the same thing.
    """
    findings = payload["findings"]
    severities = [finding["severity"] for finding in findings]
    lines = [
        "healthy" if payload["healthy"] else "problems found",
        f"  concepts: {payload['concept_count']}",
        f"  errors:   {severities.count(Severity.ERROR.value)}",
        f"  warnings: {severities.count(Severity.WARNING.value)}",
    ]
    for finding in findings:
        marker = "E" if finding["severity"] == Severity.ERROR.value else "W"
        lines.append(f"  [{marker}] {finding['code']}: {finding['message']}")
    return "\n".join(lines)


def _index(context: _Context, arguments: argparse.Namespace) -> int:
    if arguments.status:
        # A read. It never creates the database, and WAL means it does not
        # have to wait for the service either.
        return _index_status(context)
    if context.client is not None:
        try:
            return _report_run(
                context, context.client.reconcile(changed_only=arguments.changed), "index"
            )
        except ServiceTimeoutError as error:
            return _report_unheard_run(context, error, "index")
    _refuse_if_a_service_owns_the_index(context, "index")
    with _indexes(context, create=True) as indexes:
        return _report_run(
            context,
            rendering.index_run(indexes.indexer.reconcile(changed_only=arguments.changed)),
            "index",
        )


def _report_unheard_run(context: _Context, error: ServiceTimeoutError, verb: str) -> int:
    """Say what the index shows when the answer never arrived.

    The service reconciles inside the request handler, so a client that stops
    waiting has learned nothing about the work, which may well have completed.

    So the state is read back before anything is claimed, which is the
    discipline `details/openproject-adapter.md` already imposes on writes for
    the same reason: an operation whose result you did not observe is not an
    operation you may report on. The counts a run carries -- indexed,
    unchanged, removed -- are deliberately absent rather than zeroed: they
    belong to a reply nobody received, and a fabricated run is worse than a
    missing one.

    A current index exits zero: the work is done, whoever heard about it. A
    stale one exits non-zero, because that really is unresolved.
    """
    if not _index_exists(context):
        context.reporter.fail(
            StructuredError(
                "index_not_built",
                str(error),
                {"answered": False},
                retryable=True,
                repair_hint="run `never4ga index --status` once the service settles",
            )
        )
        return EXIT_FAILED
    with _indexes(context) as indexes:
        health = indexes.indexer.health()
    payload = {
        "answered": False,
        "stale": health.is_stale,
        "indexed_documents": health.indexed_documents,
        "last_indexed_at": health.last_indexed_at,
        "new": len(health.new),
        "changed": len(health.changed),
        "missing": len(health.missing),
    }
    if health.is_stale:
        context.reporter.fail(
            StructuredError(
                "reconcile_unconfirmed",
                f"{error}; the index is still behind the vault: "
                f"{len(health.new)} new, {len(health.changed)} changed, "
                f"{len(health.missing)} missing",
                payload,
                retryable=True,
                # Never `--local`: a service that has not answered still owns
                # the index, and inviting a second writer is the one thing
                # this situation must not do.
                repair_hint="the service is probably still working; "
                "check `never4ga index --status` before running it again",
            )
        )
        return EXIT_FAILED
    context.reporter.emit(
        payload,
        f"{verb}: the service did not answer in time, and the index is current\n"
        f"  documents: {health.indexed_documents}\n"
        f"  indexed:   {health.last_indexed_at}",
    )
    return EXIT_OK


def _rebuild(context: _Context, _: argparse.Namespace) -> int:
    """Throw the projections away and build them again from Markdown.

    `details/data-indexing-maintenance.md` section 17 as a command: nothing
    durable lives only in the index.
    """
    if context.client is not None:
        return _report_run(context, context.client.rebuild(), "rebuild")
    _refuse_if_a_service_owns_the_index(context, "rebuild")
    with _indexes(context, create=True) as indexes:
        run = indexes.indexer.rebuild()
        # Rebuild already means "throw it away and build it again", so it also
        # reclaims the space; without this the file keeps every freed page.
        sqlite_compact(indexes.connection)
        return _report_run(context, rendering.index_run(run), "rebuild")


class _ServiceOwnsTheIndexError(Never4gaError):
    """A second writer was asked for while the service is the writer."""


#: Unit states that mean a service is up or on its way up, and therefore may be
#: holding the index. Deliberately not `is_running`: STARTING is the whole
#: point, and it is the state `is_running` excludes.
_STARTING_STATES: Final = frozenset({ServiceState.RUNNING, ServiceState.STARTING})


def _is_the_configured_vault(context: _Context) -> bool:
    """Whether the running unit would be serving *this* vault.

    A service that has not bound its port cannot report a `vault_id`, so the
    machine-local config is the only thing that says which vault its unit is
    for. Unknown means no: refusing on a guess would break every vault that is
    not the configured one.
    """
    configured = LocalConfig.load().vault
    if configured is None:
        return False
    try:
        return configured.expanduser().resolve() == context.root.resolve()
    except OSError:
        return False


def _refuse_if_a_service_owns_the_index(context: _Context, verb: str) -> None:
    """Refuse an in-process write while a service owns this vault's index.

    Reached only with ``--local``, because without it the command would have
    gone to the service. core/05 section 10 gives the service SQLite write
    coordination, and a `never4ga index` racing the watcher is a race whether
    or not WAL lets both survive it. Reads are untouched: concurrent readers
    are what WAL is for.
    """
    manifest = FileSystemMarkdownStore(context.root).get_by_path(SYSTEM_MANIFEST)
    if manifest is None:
        return
    base_url = LocalConfig.load().service.base_url
    state, health = presence(base_url)
    if state is ServicePresence.ABSENT:
        # Nothing is listening, but that is not the same as nothing running.
        # `core/05` section 18 reconciles *before* uvicorn binds, so for the
        # minutes that takes the port is closed and this probe reports ABSENT
        # while the service already holds the index. Returning here on that
        # reading would end in a bare `database is locked` instead of a
        # refusal.
        #
        # The unit's own state is the signal the probe cannot carry. Only
        # RUNNING and STARTING refuse: STOPPED, NOT_INSTALLED and UNSUPPORTED
        # are the ordinary machine, and reading any of those as "maybe
        # starting" would stop every laptop without a service from indexing.
        #
        # And only for *this* vault. The ANSWERING branch below compares
        # `vault_id`, which a service that is not listening cannot report, so
        # the machine's configured vault stands in for it. Without that, a unit
        # running for one vault would refuse a local index of every other.
        if _service_manager().status().state in _STARTING_STATES and _is_the_configured_vault(
            context
        ):
            raise _ServiceOwnsTheIndexError(
                f"a service for this vault is starting and nothing is listening at "
                f"{base_url} yet, so `{verb} --local` would be a second writer -- a "
                "service reconciles before it serves, and owns the index while it does"
            )
        return
    if state is ServicePresence.UNREACHABLE:
        # It is listening and it did not answer -- which is what a service
        # looks like while `core/05` section 18 reconciles before uvicorn
        # accepts requests. That is precisely when it holds the index and is
        # least able to say so, so this is the one branch that must fail
        # closed. Treating silence as permission is how a starting service
        # acquires a second writer.
        raise _ServiceOwnsTheIndexError(
            f"something is listening at {base_url} and has not answered, so `{verb} --local` "
            "cannot prove it would not be a second writer -- a service reconciles before it "
            "serves, and owns the index while it does"
        )
    if health is None or health.get("vault_id") != str(manifest.concept_id):
        return
    raise _ServiceOwnsTheIndexError(
        f"a service is running for this vault and owns its index, so `{verb} --local` "
        "would be a second writer"
    )


def _report_run(context: _Context, payload: dict[str, Any], verb: str) -> int:
    context.reporter.emit(
        payload,
        "\n".join(
            [
                f"{verb}: {payload['indexed']} indexed, {payload['unchanged']} unchanged, "
                f"{payload['removed']} removed",
                *_foreign_run_line(payload.get("foreign")),
                *(f"  [W] {issue['code']}: {issue['message']}" for issue in payload["issues"]),
            ]
        ),
    )
    return EXIT_OK


def _foreign_run_line(foreign: Any) -> list[str]:
    """The foreign notes a run touched, when it touched any (core/02 section 3.3).

    Silent in a vault with no foreign material. ``.get`` because a service
    older than this build answers without the key.
    """
    if not isinstance(foreign, dict) or not any(foreign.values()):
        return []
    return [
        f"  foreign notes: {foreign['indexed']} indexed, {foreign['unchanged']} unchanged, "
        f"{foreign['removed']} removed"
    ]


def _index_status(context: _Context) -> int:
    """Report what the index knows. Reads never repair."""
    if not _index_exists(context):
        context.reporter.emit(
            {"built": False, "stale": True},
            "no index yet\n  run `never4ga index` to build one",
        )
        return EXIT_FAILED
    with _indexes(context) as indexes:
        health = indexes.indexer.health()
        database = indexes.database
    context.reporter.emit(
        {
            "built": True,
            "database": str(database),
            "indexed_documents": health.indexed_documents,
            "indexed_foreign": health.indexed_foreign,
            "last_indexed_at": health.last_indexed_at,
            "stale": health.is_stale,
            "new": [str(path) for path in health.new],
            "changed": [str(path) for path in health.changed],
            "missing": [str(path) for path in health.missing],
            "broken_links": [
                {
                    "source": str(link.source),
                    "target": link.target,
                    "named": link.target_name is not None,
                }
                for link in health.broken_links
            ],
            "unresolved_relations": [
                {
                    "source": str(relation.source),
                    "relation_type": relation.relation_type,
                    "target": str(relation.target),
                }
                for relation in health.unresolved_relations
            ],
        },
        _index_summary(health, database),
    )
    return EXIT_OK if not health.is_stale else EXIT_FAILED


def _index_summary(health: IndexHealth, database: Path) -> str:
    lines = [
        "stale" if health.is_stale else "current",
        f"  database:  {database}",
        f"  documents: {health.indexed_documents}",
        *([f"  foreign:   {health.indexed_foreign}"] if health.indexed_foreign else []),
        f"  indexed:   {health.last_indexed_at or 'never'}",
    ]
    for label, paths in (
        ("new", health.new),
        ("changed", health.changed),
        ("missing", health.missing),
    ):
        lines.extend(f"  {label:<10} {path}" for path in paths)
    lines.extend(
        f"  [W] broken link: {link.target} (from {link.source})" for link in health.broken_links
    )
    lines.extend(
        f"  [W] relation to nothing: {relation.relation_type} -> {relation.target}"
        for relation in health.unresolved_relations
    )
    return "\n".join(lines)


def _search(context: _Context, arguments: argparse.Namespace) -> int:
    request = {
        "query": " ".join(arguments.query),
        "types": list(arguments.type or ()),
        "tags": list(arguments.tag or ()),
        "domains": list(arguments.domain or ()),
        "workspace_ids": [arguments.workspace] if arguments.workspace else [],
        "limit": arguments.limit,
        "explain": arguments.explain,
    }
    if context.client is not None:
        payload = context.client.search(request)
    else:
        if not _index_exists(context):
            context.reporter.fail(
                StructuredError(
                    "index_not_built",
                    "this vault has not been indexed yet",
                    {"vault": str(context.root)},
                    repair_hint="run `never4ga index`",
                )
            )
            return EXIT_FAILED
        payload = _local_search(context, request)

    context.reporter.emit(payload, _search_summary(payload))
    return EXIT_OK


def _local_search(context: _Context, request: dict[str, Any]) -> dict[str, Any]:
    with _indexes(context) as indexes:
        response = indexes.searcher.search(
            SearchRequest(
                query=request["query"],
                types=tuple(request["types"]),
                tags=tuple(request["tags"]),
                domains=tuple(request["domains"]),
                workspace_ids=tuple(ConceptId.parse(raw) for raw in request["workspace_ids"]),
                limit=request["limit"],
            )
        )
    return rendering.search_response(response, explain=bool(request.get("explain")))


def _search_summary(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    if payload["index_is_stale"]:
        # Reported, never repaired: the user decides when to reindex.
        lines.append("[W] the index is stale; run `never4ga index`")
    if not payload["results"]:
        lines.append("no results")
    for result in payload["results"]:
        location = str(result["path"])
        if result["lines"]:
            location += f":{result['lines'][0]}-{result['lines'][1]}"
        heading = " > ".join(result["heading_path"])
        lines.append(f"{result['rank']}. {result['title']}  ({result['reason']})")
        lines.append(f"   {location}{'  # ' + heading if heading else ''}")
        lines.append(f"   {result['excerpt']}")
        if result.get("explain"):
            lanes = ", ".join(
                f"{lane} #{rank}" for lane, rank in sorted(result["explain"]["lanes"].items())
            )
            lines.append(f"   score {result['explain']['score']:.5f}  <- {lanes or 'no lane'}")
    return "\n".join(lines)


def _concept_get(context: _Context, arguments: argparse.Namespace) -> int:
    """Read one concept. Markdown is canonical; the index only adds to it."""
    if context.client is not None:
        payload = context.client.concept(arguments.concept)
        context.reporter.emit(payload, _concept_summary(payload))
        return EXIT_OK

    try:
        concept_id = ConceptId.parse(arguments.concept)
    except IdentityError:
        note = foreign_note_at(context.documents, arguments.concept)
        if note is None:
            raise
        context.reporter.fail(not_a_concept(note))
        return EXIT_FAILED
    view: ConceptView | None
    if _index_exists(context):
        with _indexes(context) as indexes:
            view = indexes.searcher.get(concept_id)
    else:
        # No projection, and none needed: Markdown is canonical (core/00 #1),
        # so a concept is readable before anything has ever been indexed.
        document = context.documents.get(concept_id)
        view = None if document is None else ConceptView(document=document, index_is_stale=True)

    if view is None:
        context.reporter.fail(
            StructuredError(
                "concept_not_found",
                f"no concept with id {arguments.concept}",
                {"id": arguments.concept},
                repair_hint="run `never4ga search` to find it",
            )
        )
        return EXIT_FAILED

    payload = {
        "id": str(view.document.concept_id),
        "path": str(view.document.path),
        "frontmatter": dict(view.document.frontmatter),
        "body": view.document.body,
        "index_is_stale": view.index_is_stale,
        "relations": {
            "outgoing": [{"id": str(n.concept_id), "type": n.relation_type} for n in view.outgoing],
            "incoming": [{"id": str(n.concept_id), "type": n.relation_type} for n in view.incoming],
        },
    }
    context.reporter.emit(payload, _concept_summary(payload))
    return EXIT_OK


def _concept_summary(payload: dict[str, Any]) -> str:
    frontmatter = payload["frontmatter"]
    relations = payload["relations"]
    lines = [
        f"{frontmatter.get('title', Path(payload['path']).name)}",
        f"  id:   {payload['id']}",
        f"  type: {frontmatter.get('type', 'unknown')}",
        f"  path: {payload['path']}",
    ]
    lines.extend(f"  --> {n['type']} {n['id']}" for n in relations["outgoing"])
    lines.extend(f"  <-- {n['type']} {n['id']}" for n in relations["incoming"])
    if payload["index_is_stale"]:
        lines.append("  [W] the index is stale; run `never4ga index`")
    lines.append("")
    lines.append(str(payload["body"]).rstrip())
    return "\n".join(lines)


def _repair(context: _Context, arguments: argparse.Namespace) -> int:
    """A plan by default, and changes only when run again with ``--apply``.

    The plan comes from *persisted* findings rather than a fresh diagnosis, so
    repair does not perform detection as a side effect of being asked to fix
    something. An empty ledger
    means nobody has looked, and says so rather than reporting a healthy vault.
    """
    if not _index_exists(context):
        raise _NotIndexableError(
            "there is no index, so no findings have been recorded; "
            "run `never4ga index` and then `never4ga doctor`"
        )
    with _indexes(context, create=False) as indexes:
        findings = [
            Finding(
                code=stored.rule,
                message=stored.message,
                severity=stored.severity,
                path=stored.path,
                document_id=stored.document_id,
            )
            for stored in indexes.findings.open_findings()
        ]
        plan = RepairPlanner().plan(findings)
        performed: list[str] = []
        if arguments.apply:
            performed = _perform(context, indexes, plan)

        payload = {
            "vault": str(context.root),
            "applied": bool(arguments.apply),
            "actions": [
                {"action": action.value, "description": describe(action)} for action in plan.actions
            ],
            "addressed": [_finding_payload(f) for f in plan.addressed],
            "unrepairable": [_finding_payload(f) for f in plan.unrepairable],
            "performed": performed,
        }
    context.reporter.emit(payload, _repair_summary(payload))
    return EXIT_OK


def _perform(context: _Context, indexes: _Indexes, plan: RepairPlan) -> list[str]:
    """Run each action, in the order the plan gives them.

    Every one of these is an existing verb, chosen for that reason: they are
    tested, idempotent, and already refuse to overwrite what a person edited.
    Repair reimplements none of them.
    """
    done = []
    for action in plan.actions:
        if action is Action.REGENERATE_NAVIGATION:
            done.append(f"regenerated {_regenerate_navigation(context)} index file(s)")
        elif action is Action.REGENERATE_VIEWS:
            count = dashboard_refresh(context.files, list(context.documents.iter_documents()))
            done.append(f"regenerated the views on {count} workspace dashboard(s)")
        elif action is Action.RESTORE_STRUCTURE:
            result = VaultInitializer(context.files, context.documents).initialize()
            done.append(f"restored {len(result.created)} missing item(s)")
        elif action is Action.REFRESH_LIBRARY:
            library = SkillLibrary(context.files)
            # No `force`: a Skill whose provenance says somebody edited it is
            # left alone and goes on being reported. An edited shipped Skill
            # is the person's now, and that is not a failure of this repair.
            changes = (
                *library.refresh(apply=True),
                *library.refresh_templates(apply=True),
            )
            done.append(f"refreshed {len(changes)} shipped document(s)")
        elif action is Action.REINDEX:
            done.append(_reindex_for_repair(context, indexes))
    return done


def _reindex_for_repair(context: _Context, indexes: _Indexes) -> str:
    """The one repair action that is not a vault write, and not ours to force.

    Repair changes the vault, which is why it runs in process. The index is
    the opposite kind of thing: derived, rebuildable, and owned by the service
    whenever one is running. Reconciling here anyway would be a second writer,
    and losing that race would raise a lock error *after* the vault half had
    succeeded, so the exit status would say the opposite of what happened.

    So the reindex follows the same rule `index` does: the service does it if
    there is one. And if it cannot happen at all, that is reported as a step
    that did not run rather than raised -- the vault repair is done and real,
    and losing that report to a derived-store failure would be the trade
    `MaintenanceLedger.reconcile_within` already declines to make.
    """
    if context.client is not None:
        try:
            payload = context.client.reconcile(changed_only=False)
        except ServiceTimeoutError:
            # The service took the work and is still doing it. Nothing is
            # wrong, and `index --status` is where the answer lands.
            return "reindex: handed to the service, which has not answered yet"
        except ServiceUnavailableError as error:
            return f"reindex: could not reach the service ({error})"
        return f"indexed {payload.get('indexed', 0)}, removed {payload.get('removed', 0)}"
    try:
        run = indexes.indexer.reconcile()
    # Broad on purpose, and for `MaintenanceLedger.reconcile_within`'s reason:
    # what a store fails with belongs to the adapter, and naming
    # `sqlite3.OperationalError` here would be a CLI that only worked against
    # one backend.
    except Exception as error:
        return f"reindex: could not update the index ({error})"
    return f"indexed {run.indexed}, removed {run.removed}"


def _regenerate_navigation(context: _Context) -> int:
    """Rewrite the managed block in every `index.md` that has drifted.

    Only the block moves. `core/01` §4 reserves `index.md` for navigation, but
    these files often also carry a person's orientation prose, and only one of the
    two is derivable -- so everything outside the markers is preserved byte for
    byte (`never4ga.services.navigation`).
    """
    return navigation_refresh(context.files, list(context.documents.iter_documents()))


def _finding_payload(finding: Finding) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity.value,
        "message": finding.message,
        "path": str(finding.path) if finding.path else None,
        "repair_hint": finding.repair_hint,
    }


def _repair_summary(payload: dict[str, Any]) -> str:
    applied = payload["applied"]
    lines = []
    if payload["actions"]:
        lines.append("repaired:" if applied else "would repair:")
        lines.extend(f"  {a['description']}" for a in payload["actions"])
        lines.extend(f"    {done}" for done in payload["performed"])
        lines.append(f"  answering {len(payload['addressed'])} finding(s)")
    else:
        lines.append("nothing to repair mechanically")
    if payload["unrepairable"]:
        # Never silent. Somebody reading this is deciding whether the vault is
        # now fine, and it is not.
        lines.append(f"needs a person ({len(payload['unrepairable'])}):")
        lines.extend(
            f"  [{f['code']}] {f['path'] or ''} -- {f['repair_hint'] or f['message']}"
            for f in payload["unrepairable"]
        )
    if not applied and payload["actions"]:
        lines.append("")
        lines.append("nothing was changed. run again with --apply to perform it.")
    return "\n".join(lines)


def _validate(context: _Context, arguments: argparse.Namespace) -> int:
    documents = context.documents
    reports = validate_vault(
        documents, level=ValidationLevel(arguments.level), paths=arguments.paths
    )
    failing = [report for report in reports if not report.ok]

    context.reporter.emit(
        {
            "checked": len(reports),
            "valid": len(reports) - len(failing),
            "documents": [
                {
                    "path": str(report.path),
                    "ok": report.ok,
                    "issues": [
                        {
                            "code": issue.code,
                            "severity": issue.severity.value,
                            "field": issue.field,
                            "message": issue.message,
                        }
                        for issue in report.issues
                    ],
                }
                for report in reports
                if report.issues
            ],
        },
        _validate_summary(reports, failing),
    )
    return EXIT_OK if not failing else EXIT_FAILED


def _validate_summary(reports: list[Any], failing: list[Any]) -> str:
    lines = [f"checked {len(reports)} concepts, {len(reports) - len(failing)} valid"]
    for report in reports:
        for issue in report.issues:
            marker = "E" if issue.severity is Severity.ERROR else "W"
            lines.append(f"  [{marker}] {report.path}: {issue.code} -- {issue.message}")
    return "\n".join(lines)


def _workspace_create(context: _Context, arguments: argparse.Namespace) -> int:
    service = _content_service(context, arguments)
    created = service.create_workspace(
        arguments.title,
        workspace_type=arguments.type,
        parent=ConceptId.parse(arguments.parent) if arguments.parent else None,
        description=arguments.description,
        lifecycle=arguments.lifecycle,
        profiles=list(arguments.profile),
        repositories=list(arguments.repository),
    )
    return _report_created(context, created, "workspace")


def _context_pack(context: _Context, arguments: argparse.Namespace) -> int:
    """A Context Pack at any of the three depths, from a service or in process.

    This one *is* service-backed: a pack is assembled from the derived index,
    which the service owns. The workspace verbs stay in process for the
    opposite reason.
    """
    depth = ContextDepth(arguments.depth)
    wants_nothing = not getattr(arguments, "terms", None) and (
        getattr(arguments, "ticket", None) is None
    )
    if depth is ContextDepth.FOCUSED and wants_nothing:
        context.reporter.fail(
            StructuredError(
                "nothing_to_focus_on",
                "a focused pack needs terms, a --ticket, or both",
                {},
                repair_hint="give some terms, or name a work item with --ticket",
            )
        )
        return EXIT_FAILED
    request: dict[str, Any] = {
        "scope": {"cwd": str(Path(arguments.path).expanduser().resolve())},
        "terms": list(getattr(arguments, "terms", None) or ()),
        "max_items": arguments.max_items,
        "max_characters": arguments.max_characters,
        "client": arguments.client,
        # The in-process path below reads `context.actor` directly, so this
        # key is for the service-backed path alone. Without it every session
        # the service answered would open as `human:owner` whatever `--actor`
        # said.
        "actor": context.actor,
        "task": arguments.task,
        "work_item": getattr(arguments, "ticket", None),
        "refresh": bool(getattr(arguments, "refresh", False)),
    }

    try:
        if context.client is not None:
            payload = context.client.context(_ROUTE_OF_DEPTH[depth], request)
        else:
            if not _index_exists(context):
                # `init` then `startup` is the first thing anybody does, and
                # SQLite will not open a database inside a directory that does
                # not exist yet. Without this check the product's most-used
                # entry point would end in a traceback.
                #
                # A check rather than a `mkdir`: core/05 section 7 says asking
                # where derived state belongs must not create it, and creating
                # an empty index here would answer a startup with a pack that
                # found nothing, which is a worse lie than a refusal.
                context.reporter.fail(
                    StructuredError(
                        "index_not_built",
                        "this vault has not been indexed yet, so there is nothing to "
                        "assemble a pack from",
                        {"vault": str(context.root)},
                        repair_hint="run `never4ga index`",
                    )
                )
                return EXIT_FAILED
            payload = _local_context(context, depth, request)
    except (ScopeResolutionError, RepositoryMarkerError) as error:
        context.reporter.fail(scope_refusal(error, request["scope"]["cwd"]))
        return EXIT_FAILED

    payload = rendering.with_disk_paths(payload, context.root)
    context.reporter.emit(payload, _context_summary(payload))
    return EXIT_OK


def _local_context(
    context: _Context, depth: ContextDepth, request: dict[str, Any]
) -> dict[str, Any]:
    sessions = _session_store(context)
    with _indexes(context) as indexes:
        service = ContextService(
            workspaces=context.workspaces,
            metadata=indexes.metadata,
            text=indexes.text,
            graph=indexes.graph,
            documents=indexes.documents,
            # Stage F: what is checked out, and what the tracker says is open.
            # The work provider stays silent unless the workspace a request
            # resolves to declares a tracker, so a vault with none pays one
            # read of a manifest it was going to read anyway.
            signal_providers=(
                GitSignalProvider(),
                # The unprocessed Inbox pile, counted and listed so the
                # session that starts is the session that files it.
                InboxSignalProvider(context.files),
                # What another client did here recently, from sessions
                # that have not been wrapped into a log yet.
                *(() if sessions is None else (SessionSignalProvider(sessions),)),
                work_signal_provider(
                    documents=indexes.documents,
                    secrets=_secrets(),
                    trackers_database=PlatformPaths.resolve().trackers_database(
                        _require_vault_id(context)
                    ),
                ),
            ),
            sessions=sessions,
        )
        pack, resolution = service.assemble(
            ContextRequest(
                scope=ScopeRequest(cwd=PurePath(request["scope"]["cwd"])),
                depth=depth,
                budget=_context_budget(depth, request),
                terms=(*request["terms"], *terms_from_task(request["task"] or "")),
                client=request["client"],
                actor=context.actor,
                task_given=request["task"] is not None,
                work_item=request["work_item"],
                refresh=request["refresh"],
            )
        )
    return rendering.scope_pack(depth.value, pack, resolution)


def _context_budget(depth: ContextDepth, request: dict[str, Any]) -> ContextBudget:
    """This depth's budget, with anything the caller stated overriding it.

    A caller who asks for depth without also restating a ceiling means the
    ceiling that depth was given, so the flags default to unset rather than to
    the startup numbers.
    """
    default = deep_budget() if depth is ContextDepth.DEEP else startup_budget()
    return ContextBudget(
        max_items=request["max_items"] or default.max_items,
        max_characters=request["max_characters"] or default.max_characters,
        max_full_documents=default.max_full_documents,
        category_limits=default.category_limits,
        response_limit=default.response_limit,
    )


def _context_summary(payload: dict[str, Any]) -> str:
    scope = payload["scope"]
    usage = payload["usage"]
    lines = [
        f"{payload['depth']} context for {scope['workspace_path']}",
        f"  items:   {usage['items']} ({usage['characters']} characters"
        f", {usage['dropped_items']} dropped)",
    ]
    # Who asked, and that a task was named. Never the task itself: core/04
    # section 16 keeps the description out of everything downstream of
    # retrieval, and a terminal scrollback is downstream.
    asked_by = payload.get("client")
    if asked_by or payload.get("task_given"):
        named = "a task was given" if payload.get("task_given") else "no task"
        lines.append(f"  asked by: {asked_by or 'unstated'}, {named}")
    # An agent has to pass this back to `checkpoint` and `wrap`, so it has to
    # be findable in the output a human reads too, not only in the JSON.
    session = payload.get("session_id")
    if session:
        lines.append(f"  session: {session}")
    lines.extend(_required_reading_lines(payload))
    for item in payload["items"]:
        lines.append(
            f"  {item['category']:<10} {item['title']}  [{item['reason']['code']}]"
            + (f"  [stale since {item['stale_since'][:10]}]" if item.get("stale_since") else "")
            + _reading_mark(item)
        )
        # What a standard governs, under it, when its body is not in the pack:
        # the reader decides from this whether the work in hand touches it.
        # `.get` because an older service answers without the field.
        if item["is_reference"] and item.get("description"):
            lines.append(f"             {item['description']}")
    lines.extend(
        f"  signal     {signal['kind']} = {signal['value']}" for signal in payload["signals"]
    )
    # A focused pack holds only what retrieval found, so an empty one means
    # nothing matched. Said here because a bare "items: 0" reads as "the vault
    # has nothing about this" and can hide a query-parsing defect. Deep is not
    # covered: its structural lane fills the pack either way.
    if payload["depth"] == ContextDepth.FOCUSED.value and not payload["items"]:
        lines.append("  [W] nothing in scope matched; the pack is empty")
    lines.extend(f"  [W] {conflict}" for conflict in scope["conflicts"])
    lines.extend(
        f"  [W] {provider} could not be read" for provider in payload["degraded_providers"]
    )
    # The bodies, after the index. The startup Skill tells an agent to read
    # them, and titles alone would send it back for a second startup with
    # `--json`, which opens a second session. The pack is bounded, so printing
    # all of it is bounded.
    for item in payload["items"]:
        if item["is_reference"] or not item.get("body"):
            continue
        lines.extend(("", f"==> {item['category']}: {item['path']}", "", item["body"].rstrip()))
    return "\n".join(lines)


def _reading_mark(item: dict[str, Any]) -> str:
    """How to read an item: required, a reference, or neither (core/07 section 10)."""
    if item.get("required"):
        if not item["is_reference"]:
            return "  [required]"
        where = item.get("disk_path") or item["path"]
        size = item.get("size")
        costs = f", {size:,} characters" if size is not None else ""
        return f"  [required, read in full: {where}{costs}]"
    if item["is_reference"] and item["id"]:
        return _reference_mark(item)
    return ""


def _required_reading_lines(payload: dict[str, Any]) -> list[str]:
    """The required-reading total against its ceiling, and a warning past it.

    Every startup states it, so growth is seen by every session rather than
    only by whoever runs `doctor`. Nothing here cuts anything.
    """
    usage = payload["usage"]
    required = [item for item in payload["items"] if item.get("required")]
    if not required:
        return []
    total = usage.get("required_characters", 0)
    ceiling = usage.get("required_ceiling")
    lines = [
        f"  required reading: {total:,} characters (~{total / 4000:.1f}k tokens)"
        + (f" of {ceiling:,}" if ceiling else "")
    ]
    if ceiling and total > ceiling:
        largest = sorted(required, key=lambda item: item.get("size") or 0, reverse=True)[:3]
        lines.append(
            "  [W] required reading is over its ceiling; largest: "
            + ", ".join(f"{item['title']} ({item.get('size') or 0:,})" for item in largest)
        )
    return lines


def _reference_mark(item: dict[str, Any]) -> str:
    """`[reference <id>]`, and why the body was not carried when the budget says.

    ``.get`` because an older service answers without the field.
    """
    why = _REFERENCE_PHRASES.get(item.get("reference_reason") or "")
    return f"  [reference {item['id']}" + (f", {why}" if why else "") + "]"


_REFERENCE_PHRASES: Final[Mapping[str, str]] = {
    ReferenceReason.LARGER_THAN_BUDGET.value: "larger than the budget",
    ReferenceReason.BUDGET_SPENT.value: "the budget was spent",
    ReferenceReason.DOCUMENT_LIMIT.value: "the document limit was reached",
}


def _workspace_map(context: _Context, arguments: argparse.Namespace) -> int:
    """Record that a repository on this machine is a workspace.

    The user names a workspace; where it lives in the vault is looked up rather
    than typed, and so is its parent -- so the registry's parent chain is the
    vault's, and cannot drift from it by being entered twice.
    """
    document = context.documents.get(ConceptId.parse(arguments.workspace))
    if document is None or document.frontmatter.get("type") != "workspace":
        context.reporter.fail(
            StructuredError(
                "workspace_not_found",
                f"no workspace with id {arguments.workspace}",
                {"id": arguments.workspace},
                repair_hint="run `never4ga workspace list` to see what exists",
            )
        )
        return EXIT_FAILED

    requested = Path(arguments.repo).expanduser().resolve()
    root = GitRepositoryLocator().repository_root(requested)
    if root is None:
        context.reporter.fail(
            StructuredError(
                "not_a_repository",
                f"{requested} is not inside a repository",
                {"path": str(requested)},
                repair_hint="map the repository root, or run this inside a repository",
            )
        )
        return EXIT_FAILED

    parent = document.frontmatter.get("parent")
    mapping = context.workspaces.map(
        workspace_id=document.concept_id,
        workspace_path=document.path,
        repository_root=root,
        parent_id=ConceptId.parse(str(parent)) if parent else None,
        ships_agent_contract=arguments.ships_agent_contract,
    )
    context.reporter.emit(
        {
            "workspace": str(mapping.workspace_id),
            "workspace_path": str(mapping.workspace_path),
            "repository_root": str(root),
            "parent": str(mapping.parent_id) if mapping.parent_id else None,
            "ships_agent_contract": mapping.ships_agent_contract,
        },
        f"mapped {root} to {document.frontmatter.get('title')} ({mapping.workspace_id})"
        + (", which ships its own agent contract" if mapping.ships_agent_contract else ""),
    )
    return EXIT_OK


def _workspace_unmap(context: _Context, arguments: argparse.Namespace) -> int:
    workspace_id = ConceptId.parse(arguments.workspace)
    if not context.workspaces.unmap(workspace_id):
        context.reporter.fail(
            StructuredError(
                "not_mapped",
                f"workspace {workspace_id} is not mapped on this machine",
                {"id": str(workspace_id)},
                repair_hint="run `never4ga workspace mappings` to see what is",
            )
        )
        return EXIT_FAILED
    context.reporter.emit(
        {"workspace": str(workspace_id), "unmapped": True},
        f"unmapped {workspace_id}",
    )
    return EXIT_OK


def _workspace_mappings(context: _Context, _: argparse.Namespace) -> int:
    mappings = context.workspaces.mappings()
    context.reporter.emit(
        {
            "mappings": [
                {
                    "workspace": str(mapping.workspace_id),
                    "workspace_path": str(mapping.workspace_path),
                    "repository_root": (
                        str(mapping.repository_root) if mapping.repository_root else None
                    ),
                    "parent": str(mapping.parent_id) if mapping.parent_id else None,
                }
                for mapping in mappings
            ]
        },
        bullet_list(
            [f"{mapping.repository_root}  ->  {mapping.workspace_path}" for mapping in mappings],
            "no repositories are mapped on this machine",
        ),
    )
    return EXIT_OK


def _workspace_resolve(context: _Context, arguments: argparse.Namespace) -> int:
    """Answer "which workspace am I in" mechanically, or refuse to guess."""
    where = Path(arguments.path).expanduser().resolve()
    try:
        resolution = context.workspaces.resolve(ScopeRequest(cwd=where))
    except (ScopeResolutionError, RepositoryMarkerError) as error:
        context.reporter.fail(scope_refusal(error, where))
        return EXIT_FAILED

    scope = resolution.scope
    context.reporter.emit(
        {
            "workspace": str(scope.workspace_id),
            "workspace_path": str(scope.workspace_path),
            "repository_root": str(scope.repository_root) if scope.repository_root else None,
            "parent_chain": [str(parent) for parent in scope.parent_chain],
            "reason": {"code": str(scope.reason.code), "detail": scope.reason.detail},
            "conflicts": list(resolution.conflicts),
        },
        "\n".join(
            [
                f"{scope.workspace_path}",
                f"  workspace: {scope.workspace_id}",
                f"  because:   {scope.reason.code} ({scope.reason.detail})",
                f"  parents:   {len(scope.parent_chain)}",
                *(f"  conflict:  {conflict}" for conflict in resolution.conflicts),
            ]
        ),
    )
    return EXIT_OK


def _workspace_list(context: _Context, _: argparse.Namespace) -> int:
    workspaces = sorted(
        (
            document
            for document in context.documents.iter_documents()
            if document.frontmatter.get("type") == "workspace"
        ),
        key=lambda document: str(document.path),
    )
    context.reporter.emit(
        {
            "workspaces": [
                {
                    "id": str(document.concept_id),
                    "title": document.frontmatter.get("title"),
                    "path": str(document.path),
                    "workspace_type": document.frontmatter.get("workspace_type"),
                    "lifecycle": document.frontmatter.get("lifecycle"),
                    "parent": document.frontmatter.get("parent"),
                }
                for document in workspaces
            ]
        },
        bullet_list(
            [
                f"{document.frontmatter.get('lifecycle', '?'):<10} "
                f"{document.frontmatter.get('title')}  ({document.concept_id})"
                for document in workspaces
            ],
            "no workspaces yet",
        ),
    )
    return EXIT_OK


def _workspace_show(context: _Context, arguments: argparse.Namespace) -> int:
    documents = context.documents
    document = documents.get(ConceptId.parse(arguments.workspace))
    if document is None or document.frontmatter.get("type") != "workspace":
        context.reporter.fail(
            StructuredError(
                "workspace_not_found",
                f"no workspace with id {arguments.workspace}",
                {"id": arguments.workspace},
                repair_hint="run `never4ga workspace list` to see what exists",
            )
        )
        return EXIT_FAILED

    directory = workspace_directory_of(document.path)
    children = [
        other
        for other in documents.iter_documents()
        if other.frontmatter.get("parent") == str(document.concept_id)
    ]
    context.reporter.emit(
        {
            "id": str(document.concept_id),
            "path": str(document.path),
            "directory": str(directory) if directory else None,
            "frontmatter": dict(document.frontmatter),
            "children": [str(child.concept_id) for child in children],
        },
        "\n".join(
            [
                f"{document.frontmatter.get('title')}",
                f"  id:        {document.concept_id}",
                f"  type:      {document.frontmatter.get('workspace_type')}",
                f"  lifecycle: {document.frontmatter.get('lifecycle')}",
                f"  path:      {document.path}",
                f"  children:  {len(children)}",
            ]
        ),
    )
    return EXIT_OK


def _knowledge_create(context: _Context, arguments: argparse.Namespace) -> int:
    service = _content_service(context, arguments)
    created = service.create_knowledge(
        arguments.title,
        description=arguments.description,
        domains=arguments.domain or None,
        tags=arguments.tag or None,
    )
    return _report_created(context, created, "knowledge")


def _note_create(context: _Context, arguments: argparse.Namespace) -> int:
    """Write a note in one step, into the right one of two tiers.

    `capture` covers the case where classifying now would interrupt. This is the
    other one: the writer already knows what the thing is, and the ceremony --
    picking a registered type, then the folder that type lives in -- is all that
    stands between the thought and the file.

    Two tiers, and the second is why `--for` exists. Durable reusable knowledge
    goes to `30_Knowledge/Notes/`, which is flat by design. What somebody learns
    *from* a bounded piece of work is scoped to the work that produced it, as a
    `research_note` in that workspace's `Research/`, and graduates to the flat
    root when it turns out to be reusable outside it. That is the existing
    meaning of `30_Knowledge/` rather than a rule invented here.
    """
    service = _content_service(context, arguments)
    if arguments.for_workspace is None:
        created = service.create_knowledge(
            arguments.title,
            description=arguments.description,
            domains=arguments.domain or None,
            tags=arguments.tag or None,
        )
        return _report_created(context, created, "knowledge")

    workspace = _workspace_named(context, arguments.for_workspace)
    fields: dict[str, Any] = {"lifecycle": "active"}
    if arguments.domain:
        fields["domains"] = list(arguments.domain)
    if arguments.tag:
        fields["tags"] = list(arguments.tag)
    created = service.create_concept(
        "research_note",
        arguments.title,
        workspace=workspace,
        description=arguments.description,
        fields=fields,
    )
    return _report_created(context, created, "research_note")


def _workspace_named(context: _Context, named: str) -> ConceptId:
    """A workspace by title, or by id when that is what the caller has.

    A title is what a person typing at a prompt knows; an id is what the machine
    has. Both are accepted. An ambiguous title is refused rather than resolved
    by picking one -- two workspaces sharing a name would otherwise move a note
    between runs, and silently.
    """
    try:
        identity = ConceptId.parse(named)
    except ValueError, TypeError:
        identity = None
    documents = context.documents
    if identity is not None and documents.get(identity) is not None:
        return identity

    matches = [
        document
        for document in documents.iter_documents()
        if document.frontmatter.get("type") == "workspace"
        and str(document.frontmatter.get("title", "")).strip() == named.strip()
    ]
    if not matches:
        raise _NoSuchWorkspaceError(f"no workspace titled {named!r} in this vault")
    if len(matches) > 1:
        where = ", ".join(str(document.path) for document in matches)
        raise _NoSuchWorkspaceError(f"{named!r} names more than one workspace ({where})")
    return matches[0].concept_id


def _map_create(context: _Context, arguments: argparse.Namespace) -> int:
    service = _content_service(context, arguments)
    created = service.create_map(arguments.title, description=arguments.description)
    return _report_created(context, created, "map")


def _life_area_create(context: _Context, arguments: argparse.Namespace) -> int:
    service = _content_service(context, arguments)
    created = service.create_life_area(arguments.title, description=arguments.description)
    return _report_created(context, created, "life area")


def _concept_create(context: _Context, arguments: argparse.Namespace) -> int:
    """Create a concept of any registered type."""
    service = _content_service(context, arguments)
    created = service.create_concept(
        arguments.type,
        arguments.title,
        workspace=ConceptId.parse(arguments.workspace) if arguments.workspace else None,
        in_directory=(VaultPath.parse(arguments.in_directory) if arguments.in_directory else None),
        description=arguments.description,
        fields=_parse_fields(arguments.field),
        numbered=arguments.number,
        series=arguments.series,
    )
    return _report_created(context, created, arguments.type)


def _adopt(context: _Context, arguments: argparse.Namespace) -> int:
    """Make a hand-written document a tracked concept, in place."""
    service = _content_service(context, arguments)
    created = service.adopt_concept(
        _path_inside_the_vault(context.root, arguments.path),
        concept_type=arguments.type,
        fields=_parse_fields(arguments.field),
        in_directory=(VaultPath.parse(arguments.in_directory) if arguments.in_directory else None),
    )
    payload = rendering.created(created)
    moved = f"  from: {created.moved_from}\n" if created.moved_from is not None else ""
    summary = (
        f"Adopted {created.document.frontmatter['type']} "
        f"{created.document.frontmatter['title']}\n"
        f"  id:   {created.concept_id}\n"
        f"{moved}"
        f"  path: {created.path}\n"
        f"  why:  {created.placement_reason}"
    )
    if created.set_aside:
        summary += f"\n  kept: {', '.join(created.set_aside)}, under extensions.adopted"
    context.reporter.emit(payload, summary)
    return EXIT_OK


def _path_inside_the_vault(root: Path, raw: str) -> VaultPath:
    """The vault-relative path, however the caller wrote it.

    A person adopting a file they can see has one of two things in hand: the
    vault-relative path a finding printed, or the filesystem path their shell
    completes. Both name the same file, so both are accepted; anything that
    resolves outside the vault falls through to `VaultPath.parse`, whose error
    says what a vault path looks like.
    """
    candidate = Path(raw).expanduser()
    absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
    try:
        resolved = absolute.resolve()
        if resolved.is_file() and resolved.is_relative_to(root.resolve()):
            return VaultPath(resolved.relative_to(root.resolve()).parts)
    except OSError:
        pass
    return VaultPath.parse(raw)


def _concept_types(context: _Context, arguments: argparse.Namespace) -> int:
    """What the registry holds, so a person need not remember it.

    `create --help` can say a type is required and cannot say which ones
    exist, what each one needs, or that `course_assignment` carries `unit`,
    `due` and `grade`. Templates carry only a body, so this is where those
    are written down.
    """
    payload = rendering.type_registry()
    entries = payload["types"]
    if arguments.type is not None:
        entries = [entry for entry in entries if entry["name"] == arguments.type]
        if not entries:
            context.reporter.fail(
                StructuredError(
                    "unknown_type",
                    f"{arguments.type!r} is not a registered type",
                    {"type": arguments.type},
                    repair_hint="run `never4ga concept types` for the list",
                )
            )
            return EXIT_FAILED
        payload = {**payload, "types": entries}

    lines = []
    for entry in entries:
        lines.append(
            f"{entry['name']}{'' if entry['creatable'] else '   (another verb creates it)'}"
        )
        lines.append(f"  lives:     {'; '.join(entry['locations'])}")
        if not entry["location_is_binding"]:
            lines.append("             (typical rather than binding)")
        if entry["required_fields"]:
            lines.append(f"  requires:  {', '.join(entry['required_fields'])}")
        if entry["lifecycle_values"]:
            lines.append(f"  lifecycle: {' | '.join(entry['lifecycle_values'])}")
        if entry["optional_fields"]:
            optional = ", ".join(
                f"{field['name']}" + (f" ({field['kind']})" if field["kind"] else "")
                for field in entry["optional_fields"]
            )
            lines.append(f"  optional:  {optional}")
        lines.append("")
    context.reporter.emit(payload, "\n".join(lines).rstrip())
    return EXIT_OK


def _parse_fields(assignments: list[str]) -> dict[str, Any]:
    """`name=value` pairs, where repeating a name means a list of values.

    Every value stays the string that was typed. The creation service converts
    a field to the kind the vocabulary declares, for this interface and the
    API and MCP alike (`details/api-cli-mcp-contract.md` section 3).
    """
    fields: dict[str, Any] = {}
    for assignment in assignments:
        name, separator, value = assignment.partition("=")
        if not separator or not name:
            raise ConceptCreationError(f"--field takes name=value, not {assignment!r}")
        existing = fields.get(name)
        if existing is None:
            fields[name] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            fields[name] = [existing, value]
    return fields


def _workspace_mark(context: _Context, arguments: argparse.Namespace) -> int:
    """Write `.never4ga.toml` into a mapped repository.

    `details/security-configuration.md` section 7.1.
    """
    root = PurePath(Path(arguments.repo).expanduser().resolve())
    outcome = context.workspaces.mark(
        root,
        deployments=DeploymentFile(PlatformPaths.resolve().deployments_file),
        apply=arguments.apply,
    )
    summary = (
        f"Wrote {outcome.marker}\n  {outcome.content.strip()}"
        if outcome.written
        else f"Would write {outcome.marker}\n  {outcome.content.strip()}\n"
        "  (run with --apply to write it)"
    )
    context.reporter.emit(
        {
            "marker": str(outcome.marker),
            "content": outcome.content,
            "written": outcome.written,
        },
        summary,
    )
    return EXIT_OK


def _server_executable() -> Path | None:
    """Where `never4ga-mcp` actually is, as an absolute path.

    Beside this process first, because a console script is installed next to the
    interpreter that runs it and that is true whether or not anybody symlinked
    it onto `PATH`. `PATH` is the fallback rather than the answer: a client
    spawned from a desktop session does not have the shell's environment, and a
    bare name that works when registering can fail when starting.
    """
    beside = Path(sys.argv[0]).resolve().parent / "never4ga-mcp"
    if beside.is_file():
        return beside
    found = shutil.which("never4ga-mcp")
    return Path(found).resolve() if found else None


def _mcp_register(context: _Context, arguments: argparse.Namespace) -> int:
    """Tell each installed client about `never4ga-mcp`.

    By running the client's own `mcp add`, never by writing its configuration
    file: the formats differ, they change, and merge semantics are the client's
    to get right. What Never4gA owns is *which* clients and *what argv*, and
    both are visible before anything runs.
    """
    service = McpRegistrationService(
        load_descriptors(context.root / INTEGRATIONS_DIRECTORY),
        home=Path.home(),
        registry=SubprocessClientRegistry(),
        server=_server_executable(),
        vault=context.root,
    )
    outcome = service.register(apply=arguments.apply, replace=arguments.replace)
    context.reporter.emit(
        {
            "applied": outcome.applied,
            "plans": [
                {
                    "client": plan.client_id,
                    "command": list(plan.command),
                    "skipped": plan.skipped,
                }
                for plan in outcome.plans
            ],
            "outcomes": [
                {"client": one.client_id, "ok": one.ok, "detail": one.detail}
                for one in outcome.outcomes
            ],
        },
        outcome.render(),
    )
    return EXIT_OK if all(one.ok for one in outcome.outcomes) else EXIT_FAILED


def _adapter_service(context: _Context) -> AdapterService:
    """The sync engine, from the one assembly all three roots share.

    `doctor` reports adapter drift from every root, so `core/05` section 15
    puts the assembly in `composition` rather than here.
    """
    return adapter_service(context.root, context.files)


def _short(version: str | None) -> str:
    """A Skill's version reads as one; a template's is a hash and must be trimmed.

    A template carries no version field -- there is nowhere in a concept
    skeleton to put one -- so its identity is the hash of what was seeded. Full
    length that is 64 characters of noise in a status table, and the leading
    twelve are as distinguishing as anybody reading a table needs. The JSON
    payload keeps the whole value.
    """
    if not version:
        return "-"
    return version[:12] if len(version) > 20 else version


def _skills_status(context: _Context, arguments: argparse.Namespace) -> int:
    library = SkillLibrary(context.files)
    statuses = (*library.status(), *library.template_status())
    context.reporter.emit(
        {
            "skills": [
                {
                    "name": s.name,
                    "state": s.state.value,
                    "vault_version": s.vault_version,
                    "shipped_version": s.shipped_version,
                }
                for s in statuses
            ]
        },
        "\n".join(
            f"  {s.state.value:<20} {s.name:<22} "
            f"{_short(s.vault_version)} -> {_short(s.shipped_version)}"
            for s in statuses
        )
        or "no canonical Skills",
    )
    return EXIT_OK


def _skills_provenance(context: _Context, _: argparse.Namespace) -> int:
    """Where every vendored Skill and agent came from, and whether it still matches."""
    service = VendoringService(context.files, context.documents)
    rows = []
    for subject in service.subjects():
        state = service.state_of(subject)
        record = state.recorded
        rows.append(
            {
                "subject": subject,
                "recorded": record is not None,
                "origin": record.origin.url or str(record.origin.kind) if record else None,
                "commit": record.origin.commit if record else None,
                "drift": str(state.drift()),
                "hazards": len(service.hazards(subject)),
            }
        )
    context.reporter.emit(
        {"subjects": rows},
        "\n".join(
            f"  {'recorded' if row['recorded'] else 'unrecorded':<12} "
            f"{row['subject']:<44} {row['drift']}"
            + (f"  [{row['hazards']} hazard(s)]" if row["hazards"] else "")
            for row in rows
        )
        or "no vendored material",
    )
    return EXIT_OK


def _skills_new(context: _Context, arguments: argparse.Namespace) -> int:
    """Start a Skill of ours: the file from the template, the record beside it."""
    author = SkillAuthor(
        context.files,
        VendoringService(context.files, context.documents),
        actor=context.actor,
    )
    try:
        authored = author.author(arguments.name, arguments.description)
    except SkillAuthoringError as error:
        context.reporter.fail(
            StructuredError(
                "cannot_author_skill",
                str(error),
                repair_hint="pick another name, or edit the Skill that is already there",
            )
        )
        return EXIT_FAILED
    context.reporter.emit(
        {
            "name": authored.name,
            "path": str(authored.path),
            "provenance": str(authored.provenance),
        },
        f"started {authored.name}\n  skill:      {authored.path}\n"
        f"  provenance: {authored.provenance}\n"
        "  fill in every <angle-bracketed> line, then run `never4ga adapters sync`",
    )
    return EXIT_OK


def _skills_provenance_record(context: _Context, arguments: argparse.Namespace) -> int:
    """Record where a vendored directory came from.

    The hazard check runs first. Recording hazardous material would write
    down that it arrived and say nothing about what it carries.
    """
    service = VendoringService(context.files, context.documents)
    subject = arguments.subject
    service.refuse_if_hazardous(subject)
    origin = Origin(
        kind=OriginKind(arguments.kind),
        fetched_at=format_timestamp(utc_now()),
        url=arguments.url,
        ref=arguments.ref,
        commit=arguments.commit,
        subpath=arguments.subpath,
        license=arguments.license,
        author=arguments.author,
    )
    record = service.record(subject, origin)
    if not arguments.apply:
        context.reporter.emit(
            {"subject": subject, "tree_digest": record.integrity.tree_digest, "applied": False},
            f"would record {subject}\n  digest: {record.integrity.tree_digest}\n"
            f"  files:  {len(record.integrity.files)}",
        )
        return EXIT_OK
    identity = ConceptId.new()
    path = service.write(
        record,
        concept_id=identity,
        base=service.base_frontmatter(record, concept_id=identity, actor=context.actor),
    )
    context.reporter.emit(
        {"subject": subject, "path": str(path), "applied": True},
        f"recorded {subject}\n  at: {path}",
    )
    return EXIT_OK


def _skills_refresh(context: _Context, arguments: argparse.Namespace) -> int:
    """Carry improved Skills into a vault that already exists.

    Dry run by default, for the reason `adapters sync` is: the target is a
    Git-backed vault whose Skills the user is entitled to have edited, and
    core/04 section 25 rule 7 wants a dry run available wherever ownership is
    at stake.
    """
    library = SkillLibrary(context.files)
    changes = (
        *library.refresh(apply=arguments.apply, force=arguments.force),
        *library.refresh_templates(apply=arguments.apply, force=arguments.force),
    )
    blocked = (
        []
        if arguments.force
        else [s for s in (*library.status(), *library.template_status()) if s.needs_a_person]
    )

    lines = [
        f"  {'refreshed' if change.applied else 'would refresh'}  {change.name:<22} {change.detail}"
        for change in changes
    ]
    lines.extend(
        f"  left alone     {s.name:<22} {s.state.value}; --force replaces it" for s in blocked
    )
    context.reporter.emit(
        {
            "applied": arguments.apply,
            "changes": [
                {
                    "name": c.name,
                    "state": c.state.value,
                    "applied": c.applied,
                    "detail": c.detail,
                }
                for c in changes
            ],
            "left_alone": [{"name": s.name, "state": s.state.value} for s in blocked],
        },
        "\n".join(lines) or "every canonical Skill is current",
    )
    return EXIT_OK


def _adapters_list(context: _Context, arguments: argparse.Namespace) -> int:
    statuses = _adapter_service(context).clients()
    context.reporter.emit(
        {
            "clients": [
                {
                    "id": status.client_id,
                    "title": status.descriptor.title,
                    "installed": status.installed,
                    "skills_directory": str(status.skills_directory),
                    "deployment": status.descriptor.deployment.value,
                    "measured_on": status.descriptor.measured_on,
                }
                for status in statuses
            ]
        },
        "\n".join(
            f"  {'installed' if s.installed else 'absent   '}  {s.client_id:<18}"
            f"{s.skills_directory}"
            for s in statuses
        )
        or "no client descriptors",
    )
    return EXIT_OK


def _pointer_sync(context: _Context, *, adopt: bool = False) -> RepositoryPointerSync:
    """Compose the repository-pointer half of sync.

    `details/agent-instruction-layering.md` section 3.2. It reads the same
    descriptors the Skill half does, because
    which repo-side filenames must be occupied is a fact about the clients on
    this machine.
    """
    return RepositoryPointerSync(
        GitRepositoryLocator(),
        context.workspaces.mappings(),
        load_descriptors(context.root / INTEGRATIONS_DIRECTORY),
        adopt=adopt,
        # core/09 section 13: an adoption is recorded, not just done. The
        # registry is machine-local, as the checkout it describes is.
        registry=ExtensionRegistryFile(PlatformPaths.resolve().extensions_file) if adopt else None,
    )


def _adapters_sync(context: _Context, arguments: argparse.Namespace) -> int:
    """Show the diff; write only with --apply.

    core/04 section 7 step 4 makes dry-run opt-in. This inverts it, because the
    target is a user's home directory across every client they have installed.
    """
    if arguments.adopt and not arguments.repositories:
        context.reporter.fail(
            StructuredError(
                "adopt_needs_repositories",
                "--adopt replaces a file in a repository, so it needs --repositories",
                repair_hint="add --repositories to say that repository files may be written",
            )
        )
        return EXIT_USAGE
    service = _adapter_service(context)
    plans = service.plan(arguments.client)
    actions = [(plan.client_id, action) for plan in plans for action in plan.actions]

    # The repository-pointer half. A pointer is not deployed *to a client*, so it
    # is not filtered by --client: the file is the same whichever client is
    # about to read it.
    pointers = _pointer_sync(context, adopt=arguments.adopt)
    pointer_plans = pointers.plan()

    if arguments.apply:
        # Deploying to clients never writes into a repository by itself: that
        # is asked for by name (details/security-configuration.md section 7.1).
        written = pointers.apply(pointer_plans) if arguments.repositories else ()
        outcomes = service.apply(plans)
        applied = [outcome for outcome in outcomes if outcome.applied]
        context.reporter.emit(
            {
                "applied": [
                    {
                        "client": outcome.action.client_id,
                        "name": outcome.action.name,
                        "kind": str(outcome.action.kind),
                        "target": outcome.detail,
                    }
                    for outcome in applied
                ],
                "unchanged": len(outcomes) - len(applied),
                "repositories_applied": bool(arguments.repositories),
                "pointers_written": [
                    {"repository": str(p.repository_root), "file": p.relative} for p in written
                ],
                "adoptions_recorded": [
                    {"repository": str(p.repository_root), "file": p.relative}
                    for p in written
                    if p.adopts
                ],
            },
            _sync_summary(
                [(o.action.client_id, o.action) for o in applied],
                pointer_plans=written,
                applied=True,
                repository_writes_held=(
                    0 if arguments.repositories else sum(p.writes for p in pointer_plans)
                ),
            ),
        )
        return EXIT_OK

    context.reporter.emit(
        {
            "planned": [
                {"client": client, "name": a.name, "kind": str(a.kind), "reason": a.reason}
                for client, a in actions
            ],
            "pointers": [
                {
                    "repository": str(p.repository_root),
                    "file": p.relative,
                    "action": str(p.action),
                    "reason": p.reason,
                }
                for p in pointer_plans
            ],
            "repositories_need": "--repositories",
        },
        _sync_summary(actions, pointer_plans=pointer_plans, applied=False),
    )
    return EXIT_OK


def _sync_summary(
    actions: list[tuple[str, Any]],
    *,
    pointer_plans: Sequence[PointerPlan] = (),
    applied: bool,
    repository_writes_held: int = 0,
) -> str:
    interesting = [p for p in pointer_plans if applied or p.action is not PointerAction.NOOP]
    held = (
        [f"not written: {repository_writes_held} repository file(s); add --repositories"]
        if repository_writes_held
        else []
    )
    if not actions and not interesting:
        return "\n".join([("nothing changed" if applied else "nothing to do"), *held])
    lines = ["changed:" if applied else "would change (run with --apply):"]
    lines.extend(f"  {client:<18} {a.kind:<9} {a.name}  [{a.reason}]" for client, a in actions)
    lines.extend(
        f"  {'repository':<18} {_pointer_kind(p):<9} {p.repository_root}/{p.relative}"
        f"{f'  [{p.reason}]' if p.reason else ''}"
        for p in interesting
    )
    if not applied and any(p.writes for p in interesting):
        lines.append("  (repository files are written only with --apply --repositories)")
    return "\n".join([*lines, *held])


def _pointer_kind(plan: PointerPlan) -> str:
    return "write" if plan.writes else str(plan.action).removeprefix("skip_")


def _adapters_doctor(context: _Context, arguments: argparse.Namespace) -> int:
    findings = _adapter_service(context).diagnose()
    actionable = [finding for finding in findings if finding.is_actionable]
    context.reporter.emit(
        {
            "findings": [
                {
                    "code": f.code,
                    "client": f.client_id,
                    "name": f.name,
                    "detail": f.detail,
                    "actionable": f.is_actionable,
                }
                for f in findings
            ],
            "healthy": not actionable,
        },
        "\n".join(f"  {f.code:<18} {f.client_id:<18} {f.name} {f.detail}" for f in findings)
        or "every installed client is in sync",
    )
    return EXIT_FAILED if actionable else EXIT_OK


def _wrap(context: _Context, arguments: argparse.Namespace) -> int:
    """Write the one record this session leaves behind."""
    try:
        session = SessionId.parse(arguments.session)
    except IdentityError as error:
        context.reporter.fail(
            StructuredError(
                "invalid_session",
                str(error),
                repair_hint="pass the id `context startup` reported",
            )
        )
        return EXIT_USAGE

    into: ConceptId | None = None
    if arguments.into:
        try:
            into = ConceptId.parse(arguments.into)
        except IdentityError as error:
            context.reporter.fail(
                StructuredError(
                    "invalid_target",
                    str(error),
                    repair_hint="--into takes the id of a workspace or a life area",
                )
            )
            return EXIT_USAGE

    store = _session_store(context)
    if store is None:
        raise _NotIndexableError(
            f"{context.root} has no {SYSTEM_MANIFEST}, so it is not an initialized vault"
        )
    service = WrapService(
        store,
        ContentService(context.files, context.documents, actor=_session_producer(context, session)),
        context.documents,
    )
    try:
        wrapped = service.wrap(session, title=arguments.title, into=into)
    except WorkspaceGoneError as error:
        context.reporter.fail(
            StructuredError(
                "workspace_gone",
                str(error),
                repair_hint=(
                    "pass `--into <id>` naming the workspace the session's work moved "
                    "to, or the life area that now holds it"
                ),
            )
        )
        return EXIT_FAILED
    except SessionStateError as error:
        context.reporter.fail(
            StructuredError(
                "cannot_wrap",
                str(error),
                repair_hint=(
                    "open a session with `never4ga context startup` and record "
                    "at least one `never4ga checkpoint` before wrapping"
                ),
            )
        )
        return EXIT_FAILED

    payload: dict[str, Any] = rendering.wrapped(wrapped)
    verb = "Updated" if wrapped.updated else "Wrote"
    summary = "\n".join(
        [
            f"{verb} the session log from {wrapped.checkpoints} checkpoint(s)",
            f"  id:   {wrapped.concept_id}",
            f"  path: {wrapped.document.path}",
            # A retitle renames the file, and a rename nobody is told about is
            # how a link goes stale without anybody choosing that.
            *(
                [f"  moved from: {wrapped.renamed_from}"]
                if wrapped.renamed_from is not None
                else []
            ),
            *(
                [f"  directed here from workspace {wrapped.redirected_from}, which is gone"]
                if wrapped.redirected_from is not None
                else []
            ),
            # Handed back rather than acted on. Turning one of these into a
            # decision record is a person's step, so the output names the verb
            # rather than running it.
            *(
                [
                    "",
                    f"{len(wrapped.decisions)} decision(s) declared, none written:",
                    *(f"  - {decision}" for decision in wrapped.decisions),
                    "  record one with `never4ga concept create decision`",
                ]
                if wrapped.decisions
                else []
            ),
            *(
                [
                    "",
                    f"{len(wrapped.memories)} thing(s) worth remembering:",
                    *(f"  - {memory}" for memory in wrapped.memories),
                ]
                if wrapped.memories
                else []
            ),
            # Reported, never acted on. core/04 section 37 asks what work items
            # *may need* an update, and a candidate is not an instruction.
            *(
                [
                    "",
                    f"{len(wrapped.work)} work item(s) may need an update:",
                    *(f"  - {item}" for item in wrapped.work),
                ]
                if wrapped.work
                else []
            ),
            # The one thing this command will not do quietly. `wrap` writes
            # nothing to a tracker, so somebody else must write what a ticket
            # needs; working through agents, the agent is that somebody, and an
            # unremarked gap here is how a tracker drifts out of date without
            # anybody choosing that.
            *(
                [
                    "",
                    f"{len(wrapped.outstanding_work)} of them were never written to:",
                    *(f"  - {item}" for item in wrapped.outstanding_work),
                    "  act on each with `never4ga work update --session <id> --apply`",
                    "  or `never4ga work comment --session <id> --apply`,",
                    "  then wrap again; pass --allow-open-work to close out anyway",
                ]
                if wrapped.outstanding_work and not arguments.allow_open_work
                else []
            ),
            # core/04 section 37. Asked on every wrap that was given anything,
            # declared or not: the session that forgets to declare is the one
            # it is for.
            *(
                [
                    "",
                    f"Context this session was given. {CONTEXT_QUESTION}",
                    *(f"  - {given.title}  ({given.path})" for given in wrapped.context_given),
                    "  if so, correct the document and declare it with "
                    "`never4ga checkpoint --context <ref>`",
                ]
                if wrapped.context_given
                else []
            ),
            *(
                [
                    "",
                    f"{len(wrapped.outstanding_context)} declared context document(s) "
                    "did not change:",
                    *(f"  - {path}" for path in wrapped.outstanding_context),
                    "  correct each to say what the session changed, then wrap again;",
                    "  pass --allow-open-context to close out anyway",
                ]
                if wrapped.outstanding_context and not arguments.allow_open_context
                else []
            ),
        ]
    )
    context.reporter.emit(payload, summary)
    # The log is written either way: refusing would strand the session's only
    # durable record over a tracker Never4gA does not own. The exit code is
    # what makes the gap impossible to pass over -- a wrap that reported it in
    # prose and exited zero would be read by exactly nobody.
    if wrapped.outstanding_work and not arguments.allow_open_work:
        return EXIT_FAILED
    if wrapped.outstanding_context and not arguments.allow_open_context:
        return EXIT_FAILED
    return EXIT_OK


def _checkpoint(context: _Context, arguments: argparse.Namespace) -> int:
    """Record what has happened so far, without touching the vault."""
    try:
        session = SessionId.parse(arguments.session)
    except IdentityError as error:
        context.reporter.fail(
            StructuredError(
                "invalid_session",
                str(error),
                repair_hint="pass the id `context startup` reported",
            )
        )
        return EXIT_USAGE

    store = _session_store(context)
    if store is None:
        raise _NotIndexableError(
            f"{context.root} has no {SYSTEM_MANIFEST}, so it is not an initialized vault"
        )
    try:
        recorded = SessionService(store).checkpoint(
            session,
            " ".join(arguments.note),
            actions=tuple(arguments.action),
            decisions=tuple(arguments.decision),
            memories=tuple(arguments.memory),
            work=tuple(arguments.work),
            context=tuple(arguments.context),
        )
    except SessionStateError as error:
        # The generic handler's hint is "see `never4ga doctor`", which
        # inspects the vault -- and a session is not in the vault. Pointing
        # an agent at the wrong tool is worse than saying nothing.
        context.reporter.fail(
            StructuredError(
                "unknown_session",
                str(error),
                repair_hint=(
                    "open a session with `never4ga context startup` and pass the id it reports"
                ),
            )
        )
        return EXIT_FAILED
    payload = rendering.checkpoint(recorded)
    summary = "\n".join(
        [
            f"checkpoint recorded at {recorded.recorded_at:%H:%M:%S}",
            f"  session: {recorded.session}",
            *(f"  action:   {action}" for action in recorded.actions),
            *(f"  decision: {decision}" for decision in recorded.decisions),
            *(f"  remember: {memory}" for memory in recorded.memories),
        ]
    )
    context.reporter.emit(payload, summary)
    return EXIT_OK


def _capture(context: _Context, arguments: argparse.Namespace) -> int:
    """Put something in the Inbox without deciding what it is."""
    captured = CaptureService(context.files).capture(
        " ".join(arguments.text), title=arguments.title
    )
    context.reporter.emit(
        rendering.captured(captured),
        f"Captured {captured.title}\n  path: {captured.path}",
    )
    return EXIT_OK


def _inbox_list(context: _Context, _: argparse.Namespace) -> int:
    """What is waiting to be filed."""
    pending = _inbox(context).pending()
    context.reporter.emit(
        rendering.inbox_pending(pending),
        "inbox is clear"
        if not pending
        else "\n".join([f"{len(pending)} waiting", *(f"  {path}" for path in pending)]),
    )
    return EXIT_OK


def _inbox_resolve(context: _Context, arguments: argparse.Namespace) -> int:
    """Clear one item, having said what accounted for it."""
    try:
        resolved = _inbox(context).resolve(
            VaultPath.parse(arguments.path),
            concept=ConceptId.parse(arguments.into) if arguments.into else None,
            work_item=arguments.work,
            session=SessionId.parse(arguments.session) if arguments.session else None,
            discard=arguments.discard,
            reason=arguments.reason,
        )
    except InboxError as error:
        context.reporter.fail(
            StructuredError(
                "not_accounted_for",
                str(error),
                repair_hint=error.hint
                or "file the item first, then clear it; nothing leaves the "
                "Inbox until something accounts for it",
            )
        )
        return EXIT_FAILED
    summary = (
        f"discarded {resolved.path}\n  reason: {resolved.reason}"
        if resolved.became is None
        else f"cleared {resolved.path}\n  became: {resolved.became}"
    )
    context.reporter.emit(rendering.inbox_resolved(resolved), summary)
    return EXIT_OK


def _scratch_write(context: _Context, arguments: argparse.Namespace) -> int:
    """Put one thought on the scratchpad."""
    try:
        line = _inbox(context).scratch(" ".join(arguments.text))
    except InboxError as error:
        context.reporter.fail(StructuredError("nothing_to_capture", str(error)))
        return EXIT_FAILED
    context.reporter.emit(
        rendering.scratch_written(line), f"scratchpad line {line.number}\n  {line.text}"
    )
    return EXIT_OK


def _scratch_list(context: _Context, _: argparse.Namespace) -> int:
    """Everything on the scratchpad, numbered."""
    lines = _inbox(context).scratch_lines()
    context.reporter.emit(
        rendering.scratch_lines(lines),
        "the scratchpad is empty"
        if not lines
        else "\n".join(
            [f"{len(lines)} on the scratchpad", *(f"  {n.number}. {n.text}" for n in lines)]
        ),
    )
    return EXIT_OK


def _scratch_resolve(context: _Context, arguments: argparse.Namespace) -> int:
    """Take one thought off, having said what accounted for it."""
    try:
        resolved = _inbox(context).resolve_scratch(
            arguments.number,
            concept=ConceptId.parse(arguments.into) if arguments.into else None,
            work_item=arguments.work,
            session=SessionId.parse(arguments.session) if arguments.session else None,
            discard=arguments.discard,
            reason=arguments.reason,
        )
    except InboxError as error:
        context.reporter.fail(
            StructuredError(
                "not_accounted_for",
                str(error),
                repair_hint=error.hint
                or "file the thought first, then take it off; a line leaves the "
                "scratchpad under the same accounting an Inbox item leaves under",
            )
        )
        return EXIT_FAILED
    summary = (
        f"discarded scratchpad line {arguments.number}\n  reason: {resolved.reason}"
        if resolved.became is None
        else f"cleared scratchpad line {arguments.number}\n  became: {resolved.became}"
    )
    context.reporter.emit(rendering.inbox_resolved(resolved), summary)
    return EXIT_OK


def _inbox(context: _Context) -> InboxService:
    return InboxService(context.files, context.documents, _session_store(context))


def _report_created(context: _Context, created: Created, label: str) -> int:
    payload = rendering.created(created)
    summary = (
        f"Created {label} {created.document.frontmatter['title']}\n"
        f"  id:   {created.concept_id}\n"
        f"  path: {created.path}"
    )
    if created.placement_reason:
        summary += f"\n  why:  {created.placement_reason}"
    context.reporter.emit(payload, summary)
    return EXIT_OK


# -- the service ----------------------------------------------------------


def _serve(context: _Context, arguments: argparse.Namespace) -> int:
    """Run the service in the foreground (core/05 section 10).

    The heavy imports happen here rather than at module scope: `never4ga
    search` should not pay for an HTTP stack it never touches.
    """
    from never4ga.config import ServiceEndpoint
    from never4ga.service import LocalService, ServiceSettings

    configured = LocalConfig.load().service
    endpoint = (
        ServiceEndpoint(host=configured.host, port=arguments.port)
        if arguments.port is not None
        else configured
    )
    service = LocalService(ServiceSettings(vault=context.root, endpoint=endpoint))
    try:
        report = service.start()
    except Never4gaError as error:
        context.reporter.fail(
            StructuredError(
                "service_not_started",
                str(error),
                {"vault": str(context.root)},
                repair_hint="run `never4ga doctor` against this vault",
            )
        )
        return EXIT_FAILED

    context.reporter.emit(
        {
            "vault": str(context.root),
            "vault_id": str(report.vault_id),
            "endpoint": service.base_url,
            "degraded": report.degraded,
            "steps": [
                {"name": step.name, "ok": step.ok, "detail": step.detail} for step in report.steps
            ],
            "warnings": list(report.warnings),
        },
        _serve_summary(service.base_url, report),
    )
    try:
        _wait_for_a_stop_signal()
    finally:
        service.stop()
    return EXIT_OK


def _wait_for_a_stop_signal() -> None:
    """Block until Ctrl-C or SIGTERM.

    SIGTERM is what a systemd unit sends, and handling it is what turns a
    kill into a graceful shutdown -- the watcher released, the connection
    closed, the socket freed. Neither signal is an error: both are how a
    service is meant to end.
    """
    import signal
    import threading

    stopping = threading.Event()

    def stop(_signal: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    stopping.wait()


def _serve_summary(base_url: str, report: Any) -> str:
    lines = [
        f"serving {base_url}",
        f"  vault: {report.vault_id}",
    ]
    lines.extend(f"  [W] {step.name}: {step.detail}" for step in report.steps if not step.ok)
    lines.extend(f"  [W] {warning}" for warning in report.warnings)
    lines.append("  press Ctrl-C to stop")
    return "\n".join(lines)


def _service_status(context: _Context, _: argparse.Namespace) -> int:
    """What the platform thinks, and what is actually answering.

    Both, because they disagree in the case that matters: a unit that systemd
    calls active while nothing listens is exactly what a status command is for.
    """
    status = _service_manager().status()
    endpoint = LocalConfig.load().service
    health = probe(endpoint.base_url)
    context.reporter.emit(
        {
            "state": status.state.value,
            "detail": status.detail,
            "endpoint": endpoint.base_url,
            "listening": health is not None,
            "serves_this_vault": (
                health is not None and health.get("vault_id") == _vault_id_of(context)
            ),
            "vault_id": health.get("vault_id") if health else None,
            # Asked directly rather than only discovered on delegation. A
            # daemon runs the code it was started with, so this is the one
            # place to find out without changing anything.
            "build": health.get("build") if health else None,
            "this_build": STARTUP_BUILD_ID,
            "runs_this_build": health is not None and build_matches(health),
        },
        _service_status_summary(status, endpoint.base_url, health),
    )
    return EXIT_OK


def _vault_id_of(context: _Context) -> str | None:
    manifest = FileSystemMarkdownStore(context.root).get_by_path(SYSTEM_MANIFEST)
    return str(manifest.concept_id) if manifest is not None else None


def _service_status_summary(
    status: ServiceStatus, base_url: str, health: dict[str, Any] | None
) -> str:
    lines = [
        f"unit:      {status.state}",
        f"endpoint:  {base_url}",
        f"listening: {'yes' if health else 'no'}",
    ]
    if health is not None:
        matches = build_matches(health)
        reported = health.get("build") or "too old to report one"
        lines.append(
            f"build:     {reported}{'' if matches else '  (this CLI is ' + STARTUP_BUILD_ID + ')'}"
        )
        if not matches:
            lines.append("  the service is running a different build; commands are")
            lines.append("  answered in process until it is restarted")
            lines.append("  run `never4ga service restart`")
    if status.detail:
        lines.append(f"  {status.detail}")
    if health is None and status.state is ServiceState.UNSUPPORTED:
        lines.append("  run `never4ga serve` in the foreground")
    return "\n".join(lines)


def _service_control(context: _Context, arguments: argparse.Namespace) -> int:
    manager = _service_manager()
    command: str = arguments.control
    status = getattr(manager, command)()
    context.reporter.emit(
        {"command": command, "state": status.state.value, "detail": status.detail},
        f"{command}: {status.state}\n  {status.detail}",
    )
    return EXIT_OK


# -- parser ---------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="never4ga",
        description="Markdown-first personal knowledge, context and memory.",
    )
    parser.add_argument(
        "--vault",
        default=default_vault_root(),
        help=f"vault root (default: ${VAULT_ENVIRONMENT_VARIABLE} or the current directory)",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help=(
            "who is writing, for the `generated.by` field -- an agent MUST name "
            "itself (core/02 section 5.2), e.g. claude-code/claude-opus-5; "
            f"defaults to {OWNER_ACTOR}"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human output",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "do the work in this process even if a service is running; "
            "an in-process write to derived state is refused while the "
            "service owns it"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="create the canonical vault structure")
    initialize.add_argument("--title", default="Never4gA Vault", help="vault title")
    initialize.set_defaults(handler=_init)

    status = commands.add_parser("status", help="summarize the vault")
    status.set_defaults(handler=_status)

    doctor = commands.add_parser("doctor", help="check vault health; writes nothing to the vault")
    doctor.add_argument(
        "--check-connections",
        action="store_true",
        help="also ask each configured tracker whether it answers (reaches the network)",
    )
    _add_level(doctor)
    doctor.set_defaults(handler=_doctor)

    repair = commands.add_parser(
        "repair", help="plan the repairs a finding makes obvious; changes nothing without --apply"
    )
    repair.add_argument(
        "--apply",
        action="store_true",
        help="perform the plan; the only thing that may change the vault",
    )
    repair.set_defaults(handler=_repair)

    validate = commands.add_parser("validate", help="validate concepts against Schema v0.1")
    validate.add_argument("paths", nargs="*", help="vault-relative paths (default: all)")
    _add_level(validate)
    validate.set_defaults(handler=_validate)

    _add_index_commands(commands)
    _add_workspace_commands(commands)
    _add_context_commands(commands)
    _add_connection_commands(commands)
    _add_work_commands(commands)
    _add_creation_commands(commands)
    _add_service_commands(commands)
    return parser


def _add_service_commands(commands: Any) -> None:
    serve = commands.add_parser("serve", help="run the local service in the foreground")
    serve.add_argument(
        "--port",
        type=int,
        help="override the configured port; 0 asks the operating system for one",
    )
    serve.set_defaults(handler=_serve)

    service = commands.add_parser("service", help="report on and control the background service")
    actions = service.add_subparsers(dest="action", required=True)

    status = actions.add_parser("status", help="what the platform thinks, and what is answering")
    status.set_defaults(handler=_service_status)

    for verb, help_text in (
        ("start", "start the service through the platform service manager"),
        ("stop", "stop the service"),
        ("restart", "restart the service"),
    ):
        # core/05 section 11 names install alongside these. It is not offered:
        # writing a unit file means writing into another tool's directory.
        action = actions.add_parser(verb, help=help_text)
        action.set_defaults(handler=_service_control, control=verb)


def _add_index_commands(commands: Any) -> None:
    index = commands.add_parser("index", help="bring the derived index in line with the vault")
    index.add_argument(
        "--changed",
        action="store_true",
        help="only reindex documents whose content changed",
    )
    index.add_argument(
        "--status",
        action="store_true",
        help="report what the index knows; changes nothing",
    )
    index.set_defaults(handler=_index)

    rebuild = commands.add_parser(
        "rebuild", help="discard the derived index and build it again from Markdown"
    )
    rebuild.set_defaults(handler=_rebuild)

    search = commands.add_parser("search", help="search the vault")
    search.add_argument("query", nargs="+", help='words, "quoted phrases", or an identifier')
    search.add_argument(
        "--explain",
        action="store_true",
        help="show the fused score and which lane found each result (unstable output)",
    )
    search.add_argument("--type", action="append", default=[], help="restrict to a concept type")
    search.add_argument("--tag", action="append", default=[])
    search.add_argument("--domain", action="append", default=[])
    search.add_argument("--workspace", help="id of a workspace to search within")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(handler=_search)

    skills = commands.add_parser(
        "skills", help="keep the vault's Skills and Templates current with this build"
    )
    skill_actions = skills.add_subparsers(dest="action", required=True)

    skill_status = skill_actions.add_parser(
        "status", help="where each vault Skill and Template stands against the shipped one"
    )
    skill_status.set_defaults(handler=_skills_status)

    skill_refresh = skill_actions.add_parser("refresh", help="show what refreshing would change")
    skill_refresh.add_argument(
        "--apply", action="store_true", help="actually write; without it nothing is touched"
    )
    skill_refresh.add_argument(
        "--force",
        action="store_true",
        help="also replace Skills that were edited or whose provenance is unknown",
    )
    skill_refresh.set_defaults(handler=_skills_refresh)

    new = skill_actions.add_parser(
        "new", help="start a Skill of our own from the template, with its provenance"
    )
    new.add_argument("name", help="lowercase, digits and hyphens; it becomes the directory")
    new.add_argument(
        "--description",
        required=True,
        help="one sentence a client lists, saying what it does and when to use it",
    )
    new.set_defaults(handler=_skills_new)

    provenance = skill_actions.add_parser(
        "provenance", help="where vendored Skills and agents came from (core/02 section 21.21)"
    )
    provenance_actions = provenance.add_subparsers(dest="provenance_action", required=False)
    provenance.set_defaults(handler=_skills_provenance)

    recording = provenance_actions.add_parser("record", help="record where one came from")
    recording.add_argument("subject", help="a vault path, e.g. 50_System/Skills/<name>")
    recording.add_argument(
        "--kind",
        default=OriginKind.GIT.value,
        choices=[kind.value for kind in OriginKind],
        help="how the material got here",
    )
    recording.add_argument("--url", help="the origin, for a git or http kind")
    recording.add_argument("--ref", help="what was asked for, e.g. refs/heads/main")
    recording.add_argument("--commit", help="what was actually taken; identity for a git origin")
    recording.add_argument("--subpath", help="which directory within the origin")
    recording.add_argument("--license", help="recorded so the obligation can be honoured")
    recording.add_argument("--author", help="who wrote it")
    recording.add_argument(
        "--apply", action="store_true", help="actually write; without it nothing is touched"
    )
    recording.set_defaults(handler=_skills_provenance_record)

    adapters = commands.add_parser("adapters", help="deploy Skills to agent clients")
    adapter_actions = adapters.add_subparsers(dest="action", required=True)

    listing = adapter_actions.add_parser("list", help="which clients this machine has")
    listing.set_defaults(handler=_adapters_list)

    sync = adapter_actions.add_parser("sync", help="show what deployment would change")
    sync.add_argument("--client", help="just this client")
    sync.add_argument(
        "--apply",
        action="store_true",
        help="deploy to agent clients; without it nothing is touched",
    )
    sync.add_argument(
        "--repositories",
        action="store_true",
        help=(
            "with --apply, also write instruction pointers and the .gitignore block "
            "into mapped repositories (details/security-configuration.md section 7.1)"
        ),
    )
    sync.add_argument(
        "--adopt",
        action="store_true",
        help=(
            "replace an agent instruction file Never4gA did not generate with a "
            "managed pointer; core/09 section 13 makes adoption an explicit act"
        ),
    )
    sync.set_defaults(handler=_adapters_sync)

    registering = adapter_actions.add_parser(
        "mcp", help="register never4ga-mcp with each installed client"
    )
    registering.add_argument(
        "--apply", action="store_true", help="actually run it; without this nothing is run"
    )
    registering.add_argument(
        "--replace",
        action="store_true",
        help="remove an existing registration first; the clients disagree about "
        "what a second add means",
    )
    registering.set_defaults(handler=_mcp_register)

    adapter_doctor = adapter_actions.add_parser(
        "doctor", help="what is deployed, what drifted, what collided"
    )
    adapter_doctor.set_defaults(handler=_adapters_doctor)

    wrap = commands.add_parser("wrap", help="write this session's activity log")
    wrap.add_argument("--session", required=True, help="the id `context startup` reported")
    wrap.add_argument(
        "--into",
        help=(
            "where the log belongs when the session's workspace no longer exists: the id "
            "of the workspace it became, or of the life area that now holds it"
        ),
    )
    wrap.add_argument(
        "--title", help="what the session was about; taken from the first checkpoint otherwise"
    )
    wrap.add_argument(
        "--allow-open-work",
        action="store_true",
        help="finish even though a declared work item was never written to",
    )
    wrap.add_argument(
        "--allow-open-context",
        action="store_true",
        help="finish even though a declared context document did not change",
    )
    wrap.set_defaults(handler=_wrap)

    checkpoint = commands.add_parser("checkpoint", help="record what this session has done so far")
    checkpoint.add_argument("note", nargs="+", help="what happened, briefly")
    checkpoint.add_argument("--session", required=True, help="the id `context startup` reported")
    checkpoint.add_argument(
        "--work",
        action="append",
        default=[],
        metavar="TEXT",
        help="a work item that may need an update; reported at wrap, never acted on",
    )
    checkpoint.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="REF",
        help=(
            "a context document this session changed something in, by id or vault path "
            "(`<ref>: what changed`); wrap reports it if its content did not move"
        ),
    )
    checkpoint.add_argument(
        "--action",
        action="append",
        default=[],
        help="a durable thing that happened; repeatable",
    )
    checkpoint.add_argument(
        "--decision",
        action="append",
        default=[],
        help=(
            "something decided, in your words; recorded as claimed and never "
            "interpreted, and never an accepted decision until an ADR says so"
        ),
    )
    checkpoint.add_argument(
        "--memory",
        action="append",
        default=[],
        help="something worth remembering beyond this session; repeatable",
    )
    checkpoint.set_defaults(handler=_checkpoint)

    adopt = commands.add_parser(
        "adopt",
        help=(
            "make a hand-written document a tracked concept, where it sits -- or, "
            "out of foreign material, in its type's home"
        ),
    )
    adopt.add_argument(
        "path", help="the file, vault-relative or as a filesystem path inside the vault"
    )
    adopt.add_argument("--type", help="the registered type, when the folder does not decide")
    adopt.add_argument(
        "--in",
        dest="in_directory",
        help="the folder a note in foreign material moves to, when its type's home is not meant",
    )
    adopt.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="a frontmatter field; repeatable, and repeating one name makes a list",
    )
    _add_producer_session(adopt)
    adopt.set_defaults(handler=_adopt)

    capture = commands.add_parser("capture", help="put something in the Inbox, unprocessed")
    capture.add_argument("text", nargs="+", help="the thought, in full")
    capture.add_argument("--title", help="a heading, if the first line is not one")
    capture.set_defaults(handler=_capture)

    inbox = commands.add_parser("inbox", help="what is waiting in the Inbox, and clearing it")
    inbox_actions = inbox.add_subparsers(dest="action", required=True)
    inbox_list = inbox_actions.add_parser("list", help="what is waiting to be filed")
    inbox_list.set_defaults(handler=_inbox_list)

    inbox_resolve = inbox_actions.add_parser(
        "resolve", help="clear one item, naming what accounted for it"
    )
    inbox_resolve.add_argument("path", help="the item, as a vault-relative path")
    inbox_resolve.add_argument("--into", help="the concept id it became")
    inbox_resolve.add_argument("--work", help="the work item it became")
    inbox_resolve.add_argument(
        "--session",
        help="the session whose recorded write accounts for --work",
    )
    inbox_resolve.add_argument(
        "--discard",
        action="store_true",
        help="it is not worth keeping; needs --reason",
    )
    inbox_resolve.add_argument("--reason", help="why a discarded item is not worth keeping")
    inbox_resolve.set_defaults(handler=_inbox_resolve)

    scratch = commands.add_parser(
        "scratch",
        help="a thought too small to be a note; the Inbox scratchpad",
    )
    scratch_actions = scratch.add_subparsers(dest="action", required=True)
    scratch_add = scratch_actions.add_parser("add", help="write one thought down")
    scratch_add.add_argument("text", nargs="+", help="the thought, in full")
    scratch_add.set_defaults(handler=_scratch_write)
    scratch_list = scratch_actions.add_parser("list", help="everything on the scratchpad")
    scratch_list.set_defaults(handler=_scratch_list)
    scratch_resolve = scratch_actions.add_parser(
        "resolve", help="take one line off, having said what accounted for it"
    )
    scratch_resolve.add_argument("number", type=int, help="the line, as `scratch list` numbers it")
    scratch_resolve.add_argument("--into", help="the concept id it became")
    scratch_resolve.add_argument("--work", help="the work item it became")
    scratch_resolve.add_argument(
        "--session", help="the session whose write accounts for a work item"
    )
    scratch_resolve.add_argument(
        "--discard", action="store_true", help="it is not worth keeping; say why with --reason"
    )
    scratch_resolve.add_argument("--reason", help="why a discarded thought is not worth keeping")
    scratch_resolve.set_defaults(handler=_scratch_resolve)

    concept = commands.add_parser("concept", help="work with individual concepts")
    concept_actions = concept.add_subparsers(dest="action", required=True)
    get = concept_actions.add_parser("get", help="read one concept")
    get.add_argument("concept", help="concept id")
    get.set_defaults(handler=_concept_get)

    types_action = concept_actions.add_parser(
        "types", help="the registered types, what each needs and what it may carry"
    )
    types_action.add_argument("type", nargs="?", help="one type, rather than all of them")
    types_action.set_defaults(handler=_concept_types)

    create = concept_actions.add_parser("create", help="create a concept of any registered type")
    create.add_argument("type", help="a registered type (core/02 section 21)")
    create.add_argument("title")
    create.add_argument("--workspace", help="id of the workspace it belongs to")
    create.add_argument("--in", dest="in_directory", help="the folder to create it in")
    create.add_argument("--description")
    create.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="a frontmatter field; repeatable, and repeating one name makes a list",
    )
    create.add_argument(
        "--number",
        action="store_true",
        help=(
            "a decision only: number it even though its folder has no numbered "
            "records yet; a folder that numbers them always does (core/02 section 21.11)"
        ),
    )
    create.add_argument(
        "--series",
        help="a decision only: which of its folder's ADR series it belongs to",
    )
    _add_producer_session(create)
    create.set_defaults(handler=_concept_create)


class _ProducerUnknownError(Never4gaError):
    """`--session` was given but the session cannot say who is writing.

    Its own error rather than a bare `SessionStateError` so the CLI can answer
    with `producer_unknown` and a hint naming the flag, instead of the generic
    "see `never4ga doctor`" that sends somebody to a healthy vault.
    """


def _content_service(context: _Context, arguments: argparse.Namespace) -> ContentService:
    """A :class:`ContentService` that records who is actually writing.

    Resolution is explicit, then observed, then the default:

    1. ``--actor``, which is the only way to name a model as well as a client
       and is what core/02 section 5.2's own example shows;
    2. the named session's ``actor``, which startup recorded when it was given
       ``--actor`` -- observed rather than inferred, since a session exists
       only because some client opened one;
    3. ``human:owner``, unchanged, which is right for a person typing.

    A session's ``client`` deliberately does not become the producer. core/02
    section 5.2 requires ``<producer>/<version>``, and a client id alone has no
    version -- synthesising one would fabricate the very field this is meant to
    make truthful. So a session that recorded no producer is an error naming
    the fix, not a silent fall back to `human:owner`.

    An unknown session id raises for the same reason: guessing at the moment
    the caller was trying hardest to be honest is the defect, not a kindness.
    """
    return ContentService(context.files, context.documents, actor=_producer(context, arguments))


def _session_producer(context: _Context, session: SessionId) -> str:
    """The producer a session recorded, or `human:owner` when it named none.

    `wrap`'s variant, and the difference from :func:`_producer` is deliberate:
    this never raises. Refusing to write a session's only durable record over a
    provenance field would lose the log to protect a metadata claim, which is
    the same trade `WrapService` already declines to make for outstanding work.
    A creation verb can be refused and retried; a wrap that refuses may simply
    not be run again.
    """
    if context.actor != OWNER_ACTOR:
        return context.actor
    store = _session_store(context)
    recorded = None if store is None else store.get(session)
    if recorded is None or recorded.actor == OWNER_ACTOR:
        return OWNER_ACTOR
    return recorded.actor


def _producer(context: _Context, arguments: argparse.Namespace) -> str:
    if context.actor != OWNER_ACTOR:
        return context.actor
    given = getattr(arguments, "session", None)
    if not given:
        return context.actor
    store = _session_store(context)
    session = None if store is None else store.get(SessionId.parse(given))
    if session is None:
        raise _ProducerUnknownError(
            f"session {given} was never opened here, so who is writing cannot be established"
        )
    if session.actor != OWNER_ACTOR:
        return session.actor
    named = f" (its client called itself {session.client})" if session.client else ""
    raise _ProducerUnknownError(
        f"session {given} recorded no producer{named}, so --session cannot say "
        "who is writing; give `context startup --actor` so every write in a "
        "session carries it (core/02 section 5.2)"
    )


def _add_producer_session(parser: argparse.ArgumentParser) -> None:
    """`--session` on a creation verb, so `generated.by` can be observed.

    core/02 section 5.2 makes naming the producer a MUST for an agent, and
    `--actor` is the way. The default is `human:owner`, so an agent that simply
    forgets writes a document claiming a person authored it, and nothing
    disagrees.

    A session is the one thing that is not a guess. It exists only because a
    client opened one, and the store kept both what that client called itself
    and whatever `--actor` startup was given. Same move `wrap` makes between a
    declaration and an observation.
    """
    parser.add_argument(
        "--session",
        help=(
            "the session id `context startup` reported; its client becomes "
            "`generated.by` unless --actor says otherwise (core/02 section 5.2)"
        ),
    )


def _add_level(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--level",
        choices=[level.value for level in ValidationLevel],
        default=ValidationLevel.STRICT.value,
        help="validation level (core/02 section 2.3)",
    )


def _add_workspace_commands(commands: Any) -> None:
    workspace = commands.add_parser("workspace", help="work with workspaces")
    actions = workspace.add_subparsers(dest="action", required=True)

    create = actions.add_parser("create", help="create a workspace")
    create.add_argument("title")
    create.add_argument("--type", required=True, help="workspace_type (core/03 section 7.1)")
    create.add_argument("--parent", help="id of the parent workspace")
    create.add_argument("--description")
    create.add_argument("--lifecycle", default="active")
    create.add_argument(
        "--profile",
        action="append",
        default=[],
        help="a profile beyond the base one, e.g. never4ga/workspace/software/0.1; repeatable",
    )
    create.add_argument(
        "--repository",
        action="append",
        default=[],
        help="a source repository this workspace owns, by name; repeatable",
    )
    _add_producer_session(create)
    create.set_defaults(handler=_workspace_create)

    listing = actions.add_parser("list", help="list workspaces")
    listing.set_defaults(handler=_workspace_list)

    show = actions.add_parser("show", help="show one workspace")
    show.add_argument("workspace", help="workspace id")
    show.set_defaults(handler=_workspace_show)

    mapping = actions.add_parser("map", help="map a repository on this machine to a workspace")
    mapping.add_argument("workspace", help="workspace id")
    mapping.add_argument(
        "--repo", default=".", help="a path in the repository to map (default: cwd)"
    )
    mapping.add_argument(
        "--ships-agent-contract",
        action="store_true",
        help=(
            "this repository authors and tracks its own AGENTS.md, so no pointer "
            "is written into it (details/agent-instruction-layering.md)"
        ),
    )
    mapping.set_defaults(handler=_workspace_map)

    marking = actions.add_parser("mark", help="write .never4ga.toml into a mapped repository")
    marking.add_argument("--repo", default=".", help="the repository to mark (default: cwd)")
    marking.add_argument(
        "--apply", action="store_true", help="actually write; without it nothing is touched"
    )
    marking.set_defaults(handler=_workspace_mark)

    linking = actions.add_parser(
        "link", help="link this workspace to a tracker project, creating it if needed"
    )
    linking.add_argument("connection", help="the connection's name")
    linking.add_argument(
        "--path", "--cwd", default=".", help="a path in the workspace (default: cwd)"
    )
    linking.add_argument("--identifier", help="the project slug; derived from the title if absent")
    linking.add_argument(
        "--apply", action="store_true", help="actually do it; without it nothing is touched"
    )
    linking.add_argument(
        "--create",
        action="store_true",
        help="also make the project when it does not exist yet",
    )
    linking.set_defaults(handler=_workspace_link)

    unmapping = actions.add_parser("unmap", help="forget a repository mapping")
    unmapping.add_argument("workspace", help="workspace id")
    unmapping.set_defaults(handler=_workspace_unmap)

    mappings = actions.add_parser("mappings", help="list this machine's repository mappings")
    mappings.set_defaults(handler=_workspace_mappings)

    resolve = actions.add_parser("resolve", help="which workspace a directory belongs to")
    resolve.add_argument("--path", default=".", help="the directory to resolve (default: cwd)")
    resolve.set_defaults(handler=_workspace_resolve)


def _connection_list(context: _Context, _: argparse.Namespace) -> int:
    payload = connection_list(context.documents, _secrets())
    context.reporter.emit(payload, _connections_summary(payload))
    return EXIT_OK


def _connection_health(context: _Context, arguments: argparse.Namespace) -> int:
    result = connection_health(context.documents, _secrets(), arguments.connection)
    if isinstance(result, StructuredError):
        context.reporter.fail(result)
        return EXIT_FAILED
    context.reporter.emit(result, _health_summary(result))
    return EXIT_OK if result["available"] else EXIT_FAILED


def _connection_set_token(context: _Context, arguments: argparse.Namespace) -> int:
    result = connection_set_token(context.documents, _secrets(), arguments.connection)
    if isinstance(result, StructuredError):
        context.reporter.fail(result)
        return EXIT_FAILED
    context.reporter.emit(
        result, f"stored a token for {result['connection']} as {result['secret']}"
    )
    return EXIT_OK


def _secrets() -> LocalSecretFileStore:
    return LocalSecretFileStore(PlatformPaths.resolve().secrets_file)


def _connections_summary(payload: dict[str, Any]) -> str:
    if not payload["connections"]:
        return "no connections are defined in 50_System/Integrations/"
    lines = [f"{len(payload['connections'])} connection(s)"]
    for entry in payload["connections"]:
        token = "token stored" if entry["token_stored"] else "no token on this machine"
        adapter = "" if entry["adapter"] else ", no adapter"
        lines.append(
            f"  {entry['connection']}  {entry['provider']}"
            f"  {entry['project_ref'] or '-'}  [{token}{adapter}]"
        )
    lines.extend(f"  ! {finding['code']}: {finding['detail']}" for finding in payload["findings"])
    return "\n".join(lines)


def _health_summary(payload: dict[str, Any]) -> str:
    state = "available" if payload["available"] else "unavailable"
    lines = [
        f"{payload['connection']} ({payload['provider']}) is {state}",
        f"  {payload['detail']}",
    ]
    if payload["capabilities"]:
        lines.append("  can: " + ", ".join(payload["capabilities"]))
    return "\n".join(lines)


def _add_connection_commands(commands: Any) -> None:
    connection = commands.add_parser(
        "connection", help="work with the vault's external-system connections"
    )
    actions = connection.add_subparsers(dest="action", required=True)

    listing = actions.add_parser("list", help="every connection the vault defines")
    listing.set_defaults(handler=_connection_list)

    health = actions.add_parser(
        "health", help="whether a connection answers, and what its instance can do"
    )
    health.add_argument("connection", help="the connection's name")
    health.set_defaults(handler=_connection_health)

    token = actions.add_parser(
        "set-token",
        help="store a connection's API token on this machine; never in the vault",
    )
    token.add_argument("connection", help="the connection's name")
    token.set_defaults(handler=_connection_set_token)


def _field_arguments(pairs: list[str]) -> dict[str, Any] | StructuredError:
    """`--field name=value` into a mapping, refusing anything shapeless."""
    fields: dict[str, Any] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name.strip():
            return StructuredError(
                "invalid_field",
                f"--field expects NAME=VALUE, not {pair!r}",
                {"given": pair},
            )
        fields[name.strip()] = value
    return fields


def _writing(context: _Context) -> WorkWriteService:
    return work_write_service(
        context.documents,
        context.workspaces,
        _secrets(),
        PlatformPaths.resolve().trackers_database(_require_vault_id(context)),
    )


def _bound(service: WorkWriteService, arguments: argparse.Namespace) -> Any:
    return service.bind(
        PurePath(Path(arguments.path).expanduser().resolve()), applying=arguments.apply
    )


def _write_summary(payload: dict[str, Any]) -> str:
    if payload.get("noop"):
        return f"{payload['proposal']}\n  nothing to do, so nothing was sent"
    if not payload["applied"]:
        return f"{payload['proposal']}\n  nothing was written (run with --apply to send it)"
    lines = [payload["proposal"], "  applied"]
    if "item" in payload:
        lines.append(f"  {payload['item']['ref']}  {payload['item']['url']}")
    if "activity" in payload:
        lines.append(f"  comment {payload['activity']}")
    return "\n".join(lines)


def _emit_write(context: _Context, result: Any) -> int:
    if isinstance(result, StructuredError):
        context.reporter.fail(result)
        return EXIT_FAILED
    context.reporter.emit(result, _write_summary(result))
    return EXIT_OK


def _reading(context: _Context) -> Any:
    return work_read_service(
        context.documents,
        context.workspaces,
        _secrets(),
        PlatformPaths.resolve().trackers_database(_require_vault_id(context)),
    )


def _bound_reader(context: _Context, arguments: argparse.Namespace) -> Any:
    service = _reading(context)
    return service, service.bind(PurePath(Path(arguments.path).expanduser().resolve()))


def _work_search(context: _Context, arguments: argparse.Namespace) -> int:
    service, bound = _bound_reader(context, arguments)
    if isinstance(bound, StructuredError):
        context.reporter.fail(bound)
        return EXIT_FAILED
    result = service.search(
        bound,
        terms=arguments.terms,
        statuses=arguments.status,
        limit=arguments.limit,
    )
    if isinstance(result, StructuredError):
        context.reporter.fail(result)
        return EXIT_FAILED
    context.reporter.emit(result, _work_items_summary(result))
    return EXIT_OK


def _work_get(context: _Context, arguments: argparse.Namespace) -> int:
    service, bound = _bound_reader(context, arguments)
    if isinstance(bound, StructuredError):
        context.reporter.fail(bound)
        return EXIT_FAILED
    result = service.get(bound, arguments.work_item, refresh=arguments.refresh)
    if isinstance(result, StructuredError):
        context.reporter.fail(result)
        return EXIT_FAILED
    context.reporter.emit(result, _work_item_summary(result["item"]))
    return EXIT_OK


#: What `work get` shows, in reading order, and the label each field gets.
#: `details/openproject-adapter.md` section 8 normalises twenty-three fields;
#: these are the ones a person reads a ticket for. The rest stay in `--json`,
#: which is where a caller goes when it wants all of them.
_WORK_ITEM_LINES: Final = (
    ("type", "type"),
    ("status", "status"),
    ("priority", "priority"),
    ("assignee", "assignee"),
    ("milestone", "milestone"),
    ("due_date", "due"),
    ("percent_complete", "done"),
    ("parent", "parent"),
    ("url", "url"),
)


def _work_item_summary(item: Mapping[str, Any]) -> str:
    """One work item as a person reads it.

    The description is included: without it a ticket could be listed but not
    read. `details/openproject-adapter.md` section 8 governs the rest:
    unavailable fields are omitted rather than printed as `None`.
    """
    lines = [f"{item['ref']}  {item['title']}"]
    present = [(label, item.get(name)) for name, label in _WORK_ITEM_LINES]
    shown = [(label, value) for label, value in present if value not in (None, "")]
    width = max((len(label) for label, _ in shown), default=0)
    lines += [f"  {label + ':':<{width + 1}} {value}" for label, value in shown]

    # The whole thing when there is one, the excerpt when the item came from a
    # list. `work get` fetches one item deliberately and an excerpt defeats it.
    description = item.get("description") or item.get("description_excerpt")
    if description:
        lines += ["", *(f"  {line}".rstrip() for line in _wrapped(str(description)))]
    return "\n".join(lines)


def _wrapped(text: str, width: int = 76) -> list[str]:
    """Wrap to a readable width, keeping the paragraphs the author wrote."""
    wrapped: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        words = paragraph.split()
        if not words:
            wrapped.append("")
            continue
        for word in words:
            if wrapped and wrapped[-1] and len(wrapped[-1]) + 1 + len(word) <= width:
                wrapped[-1] += f" {word}"
            else:
                wrapped.append(word)
    return wrapped


def _work_items_summary(payload: dict[str, Any]) -> str:
    if not payload["items"]:
        return f"no work items in {payload['project_ref']}"
    lines = [f"{payload['counted']} item(s) in {payload['project_ref']}"]
    lines += [
        f"  {one['ref']:>6}  {one['status'] or '-':<12}  {one['title']}" for one in payload["items"]
    ]
    return "\n".join(lines)


def _record_work_action(
    context: _Context, arguments: argparse.Namespace, result: Any, verb: str
) -> Any:
    """Tell the session a tracker write really happened.

    The deciding and the recording are `services/work_recording.py`, shared
    with MCP and the API so all three surfaces record alike. This only
    reads what the command line said.

    Returns the write's payload, carrying ``record_lost`` when the tracker
    changed and the session could not be told. Also said in English on
    stderr, because the human mode prints a summary rather than the payload
    and somebody reading it would otherwise see an unqualified success.
    """
    given = getattr(arguments, "work_item", None)
    record = record_tracker_write(
        _session_store(context),
        getattr(arguments, "session", None),
        verb=verb,
        result=result,
        given=None if given is None else str(given),
        applied=bool(getattr(arguments, "apply", False)),
    )
    if record.lost is not None:
        context.reporter.note(
            f"the tracker write succeeded and was not recorded against this session: "
            f"{record.lost}; do not retry the write, and expect `wrap` to be short it"
        )
    return noting_a_lost_record(result, record)


def _work_update(context: _Context, arguments: argparse.Namespace) -> int:
    service = _writing(context)
    bound = _bound(service, arguments)
    if isinstance(bound, StructuredError):
        context.reporter.fail(bound)
        return EXIT_FAILED
    fields = _field_arguments(arguments.field)
    if isinstance(fields, StructuredError):
        context.reporter.fail(fields)
        return EXIT_FAILED
    result = service.update(bound, arguments.work_item, fields)
    return _emit_write(context, _record_work_action(context, arguments, result, "update"))


def _work_create(context: _Context, arguments: argparse.Namespace) -> int:
    service = _writing(context)
    bound = _bound(service, arguments)
    if isinstance(bound, StructuredError):
        context.reporter.fail(bound)
        return EXIT_FAILED
    fields = _field_arguments(arguments.field)
    if isinstance(fields, StructuredError):
        context.reporter.fail(fields)
        return EXIT_FAILED
    result = service.create(bound, {"title": arguments.title, **fields})
    # Recorded like the other writes. A created item is the commonest thing an
    # Inbox capture becomes, so this is the record that flow needs most.
    return _emit_write(context, _record_work_action(context, arguments, result, "create"))


def _work_comment(context: _Context, arguments: argparse.Namespace) -> int:
    service = _writing(context)
    bound = _bound(service, arguments)
    if isinstance(bound, StructuredError):
        context.reporter.fail(bound)
        return EXIT_FAILED
    result = service.comment(bound, arguments.work_item, arguments.body, amends=arguments.amends)
    return _emit_write(context, _record_work_action(context, arguments, result, "comment"))


def _work_relate(context: _Context, arguments: argparse.Namespace) -> int:
    service = _writing(context)
    bound = _bound(service, arguments)
    if isinstance(bound, StructuredError):
        context.reporter.fail(bound)
        return EXIT_FAILED
    result = service.relate(
        bound,
        arguments.work_item,
        arguments.to,
        kind=arguments.kind,
        description=arguments.description,
    )
    return _emit_write(context, _record_work_action(context, arguments, result, "relate"))


def _workspace_link(context: _Context, arguments: argparse.Namespace) -> int:
    result = workspace_link(
        context.documents,
        context.workspaces,
        _secrets(),
        PurePath(Path(arguments.path).expanduser().resolve()),
        arguments.connection,
        identifier=arguments.identifier,
        apply=arguments.apply,
        create=arguments.create,
    )
    if isinstance(result, StructuredError):
        context.reporter.fail(result)
        return EXIT_FAILED
    if result["applied"]:
        summary = f"{result['proposal']}\n  linked; {result['url']}"
    elif result.get("blocked"):
        summary = f"{result['proposal']}\n  {result['blocked']}"
    else:
        summary = f"{result['proposal']}\n  nothing was written (run with --apply to do it)"
    context.reporter.emit(result, summary)
    return EXIT_OK


def _add_work_commands(commands: Any) -> None:
    work = commands.add_parser("work", help="change work items on the configured tracker")
    actions = work.add_subparsers(dest="action", required=True)

    def _common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--path",
            "--cwd",
            default=".",
            help="a path in the workspace whose tracker to write to (default: cwd)",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="actually send it; without this the change is only described",
        )
        parser.add_argument(
            "--session",
            help="the session this write belongs to, so `wrap` can see it happened",
        )

    searching = actions.add_parser("search", help="ask the tracker what is open")
    searching.add_argument("terms", nargs="*", help="words to search for")
    searching.add_argument(
        "--status", action="append", default=[], help="a status to filter by; repeatable"
    )
    searching.add_argument("--limit", type=int, help="how many items")
    searching.add_argument(
        "--path", "--cwd", default=".", help="a path in the workspace (default: cwd)"
    )
    searching.set_defaults(handler=_work_search)

    getting = actions.add_parser("get", help="read one work item")
    getting.add_argument("work_item", help="the tracker's id for the item")
    getting.add_argument(
        "--refresh",
        action="store_true",
        help="ask the tracker rather than answering from the cache",
    )
    getting.add_argument(
        "--path", "--cwd", default=".", help="a path in the workspace (default: cwd)"
    )
    getting.set_defaults(handler=_work_get)

    update = actions.add_parser("update", help="change fields on a work item")
    update.add_argument("work_item", help="the tracker's id for the item")
    update.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="a field to change, e.g. status=Closed; repeatable",
    )
    _common(update)
    update.set_defaults(handler=_work_update)

    create = actions.add_parser("create", help="create a work item")
    create.add_argument("title")
    create.add_argument(
        "--field", action="append", default=[], metavar="NAME=VALUE", help="repeatable"
    )
    _common(create)
    create.set_defaults(handler=_work_create)

    comment = actions.add_parser("comment", help="add a comment to a work item")
    comment.add_argument("work_item", help="the tracker's id for the item")
    comment.add_argument("body")
    comment.add_argument(
        "--amends",
        help="an existing comment's id to replace, rather than adding another",
    )
    _common(comment)
    comment.set_defaults(handler=_work_comment)

    relate = actions.add_parser("relate", help="link two work items")
    relate.add_argument("work_item", help="the tracker's id for the item")
    relate.add_argument("to", help="the tracker's id for the item to link it to")
    relate.add_argument(
        "--kind",
        default="relates",
        help=(
            "what connects them, in the tracker's own vocabulary "
            "(OpenProject: relates, duplicates, blocks, precedes, includes, requires "
            "and their reverses). The instance reports a kind it does not know"
        ),
    )
    relate.add_argument("--description", help="free text about why they are linked")
    _common(relate)
    relate.set_defaults(handler=_work_relate)


def _add_context_commands(commands: Any) -> None:
    context = commands.add_parser("context", help="assemble a bounded Context Pack")
    actions = context.add_subparsers(dest="action", required=True)

    for verb, help_text, depth in (
        ("startup", "the bounded pack a session starts from", ContextDepth.STARTUP),
        ("focus", "a pack about particular terms", ContextDepth.FOCUSED),
    ):
        action = actions.add_parser(verb, help=help_text)
        action.add_argument(
            "--path",
            "--cwd",
            default=".",
            help="where you are (default: cwd); core/04 section 16 spells it --cwd",
        )
        action.add_argument(
            "--client",
            help="which agent client is asking, e.g. claude-code (core/04 section 16)",
        )
        action.add_argument(
            "--task",
            help=(
                "a short description of the task, which becomes retrieval terms; "
                "never pass the full prompt (core/04 section 16)"
            ),
        )
        action.add_argument(
            "--depth",
            choices=[value.value for value in ContextDepth],
            default=depth.value,
            help="how far the pack reaches (core/04 section 18)",
        )
        action.add_argument(
            "--max-items",
            type=int,
            default=None,
            dest="max_items",
            help="override the ceiling this depth carries",
        )
        action.add_argument("--max-characters", type=int, default=None, dest="max_characters")
        action.add_argument(
            "--refresh",
            action="store_true",
            help="ask the tracker rather than whatever was cached",
        )
        if verb == "focus":
            action.add_argument(
                "--ticket",
                help=(
                    "an external work item to centre the pack on; "
                    "resolves through the workspace's configured tracker"
                ),
            )
            # Optional, because a ticket is a subject on its own: `--ticket` is
            # accepted with or without terms.
            action.add_argument("terms", nargs="*", help="what the pack should be about")
        action.set_defaults(handler=_context_pack)


def _add_creation_commands(commands: Any) -> None:
    note = commands.add_parser("note", help="write a note without naming a type or a folder")
    note.add_argument("title")
    note.add_argument(
        "--for",
        dest="for_workspace",
        metavar="WORKSPACE",
        help="a workspace title or id; the note is scoped to its Research/",
    )
    note.add_argument("--description")
    note.add_argument("--domain", action="append", default=[])
    note.add_argument("--tag", action="append", default=[])
    _add_producer_session(note)
    note.set_defaults(handler=_note_create)

    knowledge = commands.add_parser("knowledge", help="work with knowledge notes")
    knowledge_actions = knowledge.add_subparsers(dest="action", required=True)
    create_knowledge = knowledge_actions.add_parser("create", help="create a knowledge note")
    create_knowledge.add_argument("title")
    create_knowledge.add_argument("--description")
    create_knowledge.add_argument("--domain", action="append", default=[])
    create_knowledge.add_argument("--tag", action="append", default=[])
    _add_producer_session(create_knowledge)
    create_knowledge.set_defaults(handler=_knowledge_create)

    concept_map = commands.add_parser("map", help="work with knowledge maps")
    map_actions = concept_map.add_subparsers(dest="action", required=True)
    create_map = map_actions.add_parser("create", help="create a map")
    create_map.add_argument("title")
    create_map.add_argument("--description")
    _add_producer_session(create_map)
    create_map.set_defaults(handler=_map_create)

    life = commands.add_parser("life-area", help="work with life areas")
    life_actions = life.add_subparsers(dest="action", required=True)
    create_area = life_actions.add_parser("create", help="create a life area")
    create_area.add_argument("title")
    create_area.add_argument("--description")
    _add_producer_session(create_area)
    create_area.set_defaults(handler=_life_area_create)
