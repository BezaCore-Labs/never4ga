"""The loopback HTTP API (core/05 sections 12 and 13).

A thin client over :mod:`never4ga.services`, exactly as the CLI is. Every route
here opens a session, calls a service, and renders the result; anything that
looks like a decision about vaults belongs one layer down
(`details/api-cli-mcp-contract.md` section 1).

Three things are deliberate.

**One session per request.** The API opens a vault session for the duration of
a request and closes it afterwards, which gives each request its own SQLite
connection. WAL lets concurrent readers proceed without blocking each other; a
connection shared across FastAPI's threadpool would serialise them at best.

**Every endpoint but health requires the local credential.** core/05 section 12
asks for a bearer credential on mutating and sensitive endpoints even on
loopback, and everything here except liveness is one or the other. Health stays
open because the CLI probes it to decide whether a service is running, before
it has read any credential.

**Errors are structured.** `details/api-cli-mcp-contract.md` section 12: a code,
a message, details, whether retrying helps, and a repair hint -- including for
the failures FastAPI would otherwise render its own way.

The surface is only what services already back. Sessions, capture and
adapters are absent for that reason: an endpoint that returned a stub would
invite a client to depend on it. `set-token` is absent for a different one: a
credential does not travel to be stored, however local the listener.

**Work management covers the three item verbs and reads, not project
creation.** There is no workspace-lifecycle surface here for `create_project`
to belong to: `/v1/workspaces` reads and resolves and does not create, so an
endpoint for it would mean inventing a capability group to hold one verb.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePath
from typing import Annotated, Any, Final

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from never4ga import __version__, rendering
from never4ga.api.errors import status_for
from never4ga.api.models import (
    ConceptAdoptRequest,
    ConceptCreateRequest,
    ConceptResponse,
    ConceptWrittenResponse,
    ContextPackResponse,
    ContextRequestModel,
    DoctorResponse,
    ErrorResponse,
    HealthResponse,
    IndexRequest,
    IndexRunResponse,
    IndexStatusModel,
    ResolvedScopeModel,
    ResolveScopeRequest,
    SearchRequestModel,
    SearchResponseModel,
    ValidatedDocumentModel,
    ValidateRequest,
    ValidateResponse,
    ValidationIssueModel,
    VaultResponse,
    WorkCommentRequest,
    WorkCreateRequest,
    WorkspaceListResponse,
    WorkspaceMappingModel,
    WorkUpdateRequest,
    WorkWriteRequest,
    WorkWriteResponse,
)
from never4ga.build import STARTUP_BUILD_ID
from never4ga.context.budget import deep_budget, startup_budget
from never4ga.domain.context import (
    ContextBudget,
    ContextDepth,
    ContextRequest,
    terms_from_task,
)
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest
from never4ga.errors import (
    IdentityError,
    Never4gaError,
    StructuredError,
    VaultPathError,
)
from never4ga.ports.context_signal_provider import ContextSignalProvider
from never4ga.ports.session_store import SessionStore
from never4ga.schema import ValidationLevel
from never4ga.services import (
    ConceptCreationError,
    ContentService,
    ContextService,
    SearchRequest,
    SessionFactory,
    VaultSession,
    WorkspaceService,
    credential_matches,
    validate_vault,
)
from never4ga.services.search import foreign_note_at, not_a_concept
from never4ga.services.work_reading import WorkReadService
from never4ga.services.work_recording import noting_a_lost_record, record_tracker_write
from never4ga.services.work_writing import WorkWriteService

__all__ = ["API_PREFIX", "API_VERSION", "create_app"]

API_VERSION: Final = "v1"
API_PREFIX: Final = f"/{API_VERSION}"

_SERVICE_NAME: Final = "never4ga"

#: What the WWW-Authenticate challenge advertises (section 8 of the contract).
_CHALLENGE: Final = {"WWW-Authenticate": f'Bearer realm="{_SERVICE_NAME}"'}


class _UnauthenticatedError(Exception):
    """No usable credential was presented."""


def create_app(
    *,
    sessions: SessionFactory,
    #: Sessions for the two endpoints that write. The service serialises these
    #: against its own watcher and maintenance tiers (`core/05` section 10);
    #: `api` is an interface and does not know how, only which of its endpoints
    #: need it. Defaults to `sessions` so a composition root that coordinates
    #: nothing -- a test, a fake -- needs no second factory.
    writing_sessions: SessionFactory | None = None,
    credential: str,
    vault_id: ConceptId,
    workspaces: WorkspaceService | None = None,
    signal_providers: Sequence[ContextSignalProvider] = (),
    #: Providers that need one vault's ports to exist at all -- the tracker
    #: signals need its document store. A session is opened per request
    #: (`services/session.py`), so these are built per request too, and the
    #: composition root supplies the builder because `api` picks no adapters.
    session_signal_providers: Callable[[VaultSession], Sequence[ContextSignalProvider]]
    | None = None,
    #: Where a startup's session is recorded. Built per vault by the
    #: composition root, because `api` picks no adapters -- and required for
    #: the same reason the CLI has one: a session the CLI opened and the
    #: service did not would make `checkpoint` depend on which of them
    #: answered.
    session_store: Callable[[VaultSession], SessionStore] | None = None,
    #: The write half of work management. Built per vault by the composition
    #: root, because `api` picks no adapters: choosing `openproject` is a
    #: composition root's act and this layer is an interface. Absent means the
    #: `/v1/work` endpoints answer 501 rather than existing as stubs.
    work_writer: Callable[[VaultSession], WorkWriteService] | None = None,
    #: The read half. Separate from the writer because they are separately
    #: available: a connection may be configured read-only, and a plugin that
    #: lists work should not need write credentials to do it.
    work_reader: Callable[[VaultSession], WorkReadService] | None = None,
    version: str = __version__,
    monotonic: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """Build the app for one vault.

    ``vault_id`` is passed in rather than read from a session so that health
    answers without touching the database. core/05 section 19: a failed
    subsystem must not take the service down with it, and liveness that
    depends on the index is liveness that disappears exactly when it is most
    worth having.
    """
    started_at = monotonic()

    def require_credential(request: Request) -> None:
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not credential_matches(credential, presented.strip()):
            raise _UnauthenticatedError
        return None

    writing = writing_sessions or sessions
    authenticated = [Depends(require_credential)]

    app = FastAPI(
        title="Never4gA local API",
        version=version,
        summary="Loopback access to one Never4gA vault.",
        # CORS is disabled by not being enabled: no middleware is installed
        # (`details/security-configuration.md` section 2).
    )
    _install_error_handlers(app)

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=_SERVICE_NAME,
            api_version=API_VERSION,
            version=version,
            build=STARTUP_BUILD_ID,
            vault_id=str(vault_id),
            uptime_seconds=round(monotonic() - started_at, 3),
        )

    @app.get(
        f"{API_PREFIX}/doctor",
        response_model=DoctorResponse,
        dependencies=authenticated,
        tags=["health"],
    )
    def doctor(
        level: Annotated[ValidationLevel, Query()] = ValidationLevel.STRICT,
    ) -> DoctorResponse:
        with sessions() as session:
            return DoctorResponse.of(session.diagnose(level))

    @app.get(
        f"{API_PREFIX}/vault",
        response_model=VaultResponse,
        dependencies=authenticated,
        tags=["vault"],
    )
    def vault() -> VaultResponse:
        with sessions() as session:
            documents = list(session.documents.iter_documents())
            by_type: dict[str, int] = {}
            for document in documents:
                name = str(document.frontmatter.get("type", "unknown"))
                by_type[name] = by_type.get(name, 0) + 1
            manifest = next(
                (d for d in documents if d.concept_id == session.vault_id),
                None,
            )
            return VaultResponse(
                # A filesystem path is machine-local configuration the client
                # already has (core/05 section 9). It is not repeated here.
                vault_id=str(session.vault_id),
                title=_text(manifest.frontmatter.get("title")) if manifest else None,
                concept_count=len(documents),
                concepts_by_type=dict(sorted(by_type.items())),
                index=IndexStatusModel.of(session.indexer.health()),
            )

    @app.get(
        f"{API_PREFIX}/index/status",
        response_model=IndexStatusModel,
        dependencies=authenticated,
        tags=["vault"],
    )
    def index_status() -> IndexStatusModel:
        """What the index knows, without touching it.

        `/v1/vault` carries this too, nested in a larger payload; this route
        lets a client that only wants staleness skip the whole vault summary.
        Reads never repair, here as in the CLI.
        """
        with sessions() as session:
            return IndexStatusModel.of(session.indexer.health())

    @app.get(
        f"{API_PREFIX}/maintenance/findings",
        dependencies=authenticated,
        tags=["vault"],
    )
    def maintenance_findings() -> dict[str, Any]:
        """The open findings ledger, read only.

        Read only on purpose. Nothing acts on a dismissal yet, so an endpoint
        that accepted one would let a client write a record the product
        ignores.
        """
        with sessions() as session:
            if session.findings is None:
                return {"available": False, "findings": []}
            return {
                "available": True,
                "findings": [
                    {
                        "finding_id": finding.finding_id,
                        "rule": finding.rule,
                        "severity": finding.severity,
                        "fingerprint": finding.fingerprint,
                        "message": finding.message,
                        "detected_at": finding.detected_at,
                        "path": str(finding.path) if finding.path else None,
                    }
                    for finding in session.findings.open_findings()
                ],
            }

    @app.post(
        f"{API_PREFIX}/index/reconcile",
        response_model=IndexRunResponse,
        dependencies=authenticated,
        tags=["vault"],
    )
    def reconcile(body: IndexRequest | None = None) -> IndexRunResponse:
        request = body or IndexRequest()
        with writing() as session:
            return IndexRunResponse.of(session.indexer.reconcile(changed_only=request.changed_only))

    @app.post(
        f"{API_PREFIX}/index/rebuild",
        response_model=IndexRunResponse,
        dependencies=authenticated,
        tags=["vault"],
    )
    def rebuild() -> IndexRunResponse:
        # `details/data-indexing-maintenance.md` section 17: throwing the
        # projections away and building them again from Markdown must recover
        # everything derived.
        with writing() as session:
            return IndexRunResponse.of(session.rebuild())

    @app.post(
        f"{API_PREFIX}/concepts/search",
        response_model=SearchResponseModel,
        dependencies=authenticated,
        tags=["concepts"],
    )
    def search(body: SearchRequestModel) -> SearchResponseModel:
        with sessions() as session:
            return SearchResponseModel.of(
                session.searcher.search(
                    SearchRequest(
                        query=body.query,
                        types=tuple(body.types),
                        tags=tuple(body.tags),
                        domains=tuple(body.domains),
                        workspace_ids=tuple(
                            _concept_id(raw, field="workspace_ids") for raw in body.workspace_ids
                        ),
                        limit=body.limit,
                    )
                ),
                explain=body.explain,
            )

    @app.post(
        f"{API_PREFIX}/concepts/validate",
        response_model=ValidateResponse,
        dependencies=authenticated,
        tags=["concepts"],
    )
    def validate(body: ValidateRequest | None = None) -> ValidateResponse:
        request = body or ValidateRequest()
        with sessions() as session:
            return _validate(session, request)

    def _authoring(session: VaultSession, actor: str | None) -> ContentService:
        """The same service the CLI's creation verbs call, bound to who writes.

        `actor` is the caller's claim about itself; absent means a person, and
        the default stays `human:owner` exactly as it does at the terminal.
        """
        if actor is not None:
            return ContentService(session.files, session.documents, actor=actor)
        return ContentService(session.files, session.documents)

    @app.post(
        f"{API_PREFIX}/concepts",
        response_model=ConceptWrittenResponse,
        status_code=201,
        dependencies=authenticated,
        tags=["concepts"],
    )
    def concept_create(body: ConceptCreateRequest) -> ConceptWrittenResponse:
        """Create a concept of any registered type.

        `details/api-cli-mcp-contract.md` section 3: it wraps the creation
        service the CLI calls and carries no logic of its own.
        """
        with writing() as session:
            try:
                created = _authoring(session, body.actor).create_concept(
                    body.type,
                    body.title,
                    workspace=(
                        _concept_id(body.workspace, field="workspace") if body.workspace else None
                    ),
                    in_directory=(_vault_path(body.in_directory) if body.in_directory else None),
                    description=body.description,
                    fields=body.fields or None,
                    numbered=body.numbered,
                    series=body.series,
                )
            except ConceptCreationError as error:
                created_details: dict[str, Any] = {"type": body.type, "title": body.title}
                if error.candidates:
                    created_details["candidates"] = list(error.candidates)
                raise _ApiError(
                    422,
                    StructuredError(
                        "concept_not_created",
                        str(error),
                        created_details,
                        repair_hint="adjust the request; nothing was written",
                    ),
                ) from error
        return ConceptWrittenResponse.of(created)

    @app.post(
        f"{API_PREFIX}/concepts/adopt",
        response_model=ConceptWrittenResponse,
        dependencies=authenticated,
        tags=["concepts"],
    )
    def concept_adopt(body: ConceptAdoptRequest) -> ConceptWrittenResponse:
        """Make the Markdown already at ``path`` a tracked concept."""
        with writing() as session:
            try:
                adopted = _authoring(session, body.actor).adopt_concept(
                    _vault_path(body.path),
                    concept_type=body.type,
                    fields=body.fields or None,
                    in_directory=(_vault_path(body.in_directory) if body.in_directory else None),
                )
            except ConceptCreationError as error:
                # The candidates travel as data, so a client offers them as
                # choices rather than parsing them out of the sentence. The
                # message names no flag: this surface has none.
                details: dict[str, Any] = {"path": body.path}
                if error.candidates:
                    details["candidates"] = list(error.candidates)
                raise _ApiError(
                    422,
                    StructuredError(
                        "concept_not_adopted",
                        str(error),
                        details,
                        repair_hint=(
                            "name which type it is"
                            if error.candidates
                            else "adjust the request; the file is untouched"
                        ),
                    ),
                ) from error
        return ConceptWrittenResponse.of(adopted)

    @app.get(
        # `:path`, so a vault path reaches the handler to be refused by name
        # rather than 404ing on its slashes (core/02 section 3.3).
        f"{API_PREFIX}/concepts/{{concept_id:path}}",
        response_model=ConceptResponse,
        dependencies=authenticated,
        tags=["concepts"],
    )
    def concept(concept_id: str) -> ConceptResponse:
        try:
            identity = ConceptId.parse(concept_id)
        except IdentityError as error:
            with sessions() as session:
                note = foreign_note_at(session.documents, concept_id)
            if note is not None:
                raise _ApiError(404, not_a_concept(note)) from error
            raise _invalid_identity(concept_id, "concept_id", error) from error
        with sessions() as session:
            view = session.searcher.get(identity)
        if view is None:
            raise _not_found(concept_id)
        return ConceptResponse.of(view)

    @app.get(
        f"{API_PREFIX}/schema/types",
        dependencies=authenticated,
        tags=["schema"],
    )
    def schema_types() -> dict[str, Any]:
        """The Type Registry, so a client can offer what a person must know.

        Takes no vault and opens no session: the registry is a property of
        the schema version this build implements, and is the same answer for
        every vault it serves.
        """
        return rendering.type_registry()

    # -- workspaces and context -------------------------------------------

    def _workspaces() -> WorkspaceService:
        if workspaces is None:
            raise _unavailable("workspace resolution")
        return workspaces

    @app.get(
        f"{API_PREFIX}/workspaces",
        response_model=WorkspaceListResponse,
        dependencies=authenticated,
        tags=["workspaces"],
    )
    def workspace_list() -> WorkspaceListResponse:
        """What this machine knows about where workspaces live.

        Machine-local rather than vault content: the vault holds workspace
        identity, and only this machine knows where a repository sits on it.
        """
        return WorkspaceListResponse(
            mappings=[WorkspaceMappingModel.of(m) for m in _workspaces().mappings()]
        )

    @app.get(
        f"{API_PREFIX}/workspaces/{{workspace_id}}",
        response_model=WorkspaceMappingModel,
        dependencies=authenticated,
        tags=["workspaces"],
    )
    def workspace_show(workspace_id: str) -> WorkspaceMappingModel:
        identity = _concept_id(workspace_id, field="workspace_id")
        for mapping in _workspaces().mappings():
            if mapping.workspace_id == identity:
                return WorkspaceMappingModel.of(mapping)
        raise _not_mapped(workspace_id)

    @app.post(
        f"{API_PREFIX}/workspaces/resolve",
        response_model=ResolvedScopeModel,
        dependencies=authenticated,
        tags=["workspaces"],
    )
    def workspace_resolve(body: ResolveScopeRequest | None = None) -> ResolvedScopeModel:
        """Which workspace a location belongs to, mechanically or not at all."""
        return ResolvedScopeModel.of(
            _workspaces().resolve(_scope_request(body or ResolveScopeRequest()))
        )

    @app.post(
        f"{API_PREFIX}/context/startup",
        response_model=ContextPackResponse,
        dependencies=authenticated,
        tags=["context"],
    )
    def context_startup(body: ContextRequestModel | None = None) -> ContextPackResponse:
        return _context(body or ContextRequestModel(), ContextDepth.STARTUP)

    @app.post(
        f"{API_PREFIX}/context/focus",
        response_model=ContextPackResponse,
        dependencies=authenticated,
        tags=["context"],
    )
    def context_focus(body: ContextRequestModel) -> ContextPackResponse:
        return _context(body, ContextDepth.FOCUSED)

    @app.post(
        f"{API_PREFIX}/context/deep",
        response_model=ContextPackResponse,
        dependencies=authenticated,
        tags=["context"],
    )
    def context_deep(body: ContextRequestModel | None = None) -> ContextPackResponse:
        return _context(body or ContextRequestModel(), ContextDepth.DEEP)

    def _context(body: ContextRequestModel, depth: ContextDepth) -> ContextPackResponse:
        with sessions() as session:
            service = ContextService(
                workspaces=_workspaces(),
                metadata=session.metadata,
                text=session.text,
                graph=session.graph,
                documents=session.documents,
                sessions=None if session_store is None else session_store(session),
                signal_providers=(
                    *signal_providers,
                    *(
                        session_signal_providers(session)
                        if session_signal_providers is not None
                        else ()
                    ),
                ),
            )
            pack, resolution = service.assemble(
                ContextRequest(
                    scope=_scope_request(body.scope),
                    depth=depth,
                    budget=_budget(depth, body),
                    terms=(*body.terms, *terms_from_task(body.task or "")),
                    exact_identifiers=tuple(body.exact_identifiers),
                    client=body.client,
                    actor=body.actor,
                    task_given=body.task is not None,
                    work_item=body.work_item,
                    refresh=body.refresh,
                )
            )
        return ContextPackResponse.of(depth.value, pack, resolution)

    # -- work management (core/03 section 22). The three item verbs and reads,
    # not `create_project`: there is no workspace-lifecycle surface here for
    # it to belong to.

    def _write(
        body: WorkWriteRequest,
        action: str,
        verb: Callable[[WorkWriteService, Any], Any],
        *,
        work_item: str | None = None,
    ) -> WorkWriteResponse:
        if work_writer is None:
            raise _ApiError(
                501,
                StructuredError(
                    "work_writing_unavailable",
                    "this service was built without a work-management writer",
                    {},
                ),
            )
        with sessions() as session:
            service = work_writer(session)
            bound = service.bind(PurePath(body.cwd), applying=body.apply)
            if isinstance(bound, StructuredError):
                raise _ApiError(_write_status(bound), bound)
            result = verb(service, bound)
            # The observation `wrap` weighs against what a session declared,
            # taken here as the CLI and MCP take it. A structured error records
            # nothing, and failing to record never fails the write: a 500 for a
            # write the tracker accepted would make a client retry it.
            record = record_tracker_write(
                None if session_store is None else session_store(session),
                body.session_id,
                verb=action,
                result=result,
                given=work_item,
                applied=body.apply,
            )
            if isinstance(result, StructuredError):
                raise _ApiError(_write_status(result), result)
            return WorkWriteResponse.model_validate(noting_a_lost_record(result, record))

    @app.post(
        f"{API_PREFIX}/work/update",
        response_model=WorkWriteResponse,
        dependencies=authenticated,
        tags=["work"],
    )
    def work_update(body: WorkUpdateRequest) -> WorkWriteResponse:
        return _write(
            body,
            "update",
            lambda service, bound: service.update(bound, body.work_item, body.fields),
            work_item=body.work_item,
        )

    @app.post(
        f"{API_PREFIX}/work/create",
        response_model=WorkWriteResponse,
        dependencies=authenticated,
        tags=["work"],
    )
    def work_create(body: WorkCreateRequest) -> WorkWriteResponse:
        return _write(
            body,
            "create",
            lambda service, bound: service.create(bound, {"title": body.title, **body.fields}),
        )

    def _read_work(cwd: str, verb: Callable[[WorkReadService, Any], Any]) -> dict[str, Any]:
        if work_reader is None:
            raise _ApiError(
                501,
                StructuredError(
                    "work_reading_unavailable",
                    "this service was built without a work-management reader",
                    {},
                ),
            )
        with sessions() as session:
            service = work_reader(session)
            bound = service.bind(PurePath(cwd))
            if isinstance(bound, StructuredError):
                raise _ApiError(_write_status(bound), bound)
            result = verb(service, bound)
            if isinstance(result, StructuredError):
                raise _ApiError(_write_status(result), result)
            return dict(result)

    @app.get(
        f"{API_PREFIX}/work/items",
        dependencies=authenticated,
        tags=["work"],
    )
    def work_items(
        cwd: str,
        terms: str | None = None,
        status: Annotated[list[str] | None, Query()] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """The read half of work management, matching the CLI's.

        Without it a client could create a work item through the API but would
        have to shell out to read one back.
        """
        return _read_work(
            cwd,
            lambda service, bound: service.search(
                bound,
                terms=terms.split() if terms else [],
                statuses=status or [],
                limit=limit,
            ),
        )

    @app.get(
        f"{API_PREFIX}/work/items/{{work_item}}",
        dependencies=authenticated,
        tags=["work"],
    )
    def work_item(cwd: str, work_item: str, refresh: bool = False) -> dict[str, Any]:
        return _read_work(
            cwd, lambda service, bound: service.get(bound, work_item, refresh=refresh)
        )

    @app.post(
        f"{API_PREFIX}/work/comment",
        response_model=WorkWriteResponse,
        dependencies=authenticated,
        tags=["work"],
    )
    def work_comment(body: WorkCommentRequest) -> WorkWriteResponse:
        return _write(
            body,
            "comment",
            lambda service, bound: service.comment(
                bound, body.work_item, body.body, amends=body.amends
            ),
            work_item=body.work_item,
        )

    return app


# -- helpers --------------------------------------------------------------


def _budget(depth: ContextDepth, body: ContextRequestModel) -> ContextBudget:
    """The budget for this depth, with anything the caller stated overriding it.

    Defined by depth rather than by endpoint so the CLI, the API and MCP cannot
    answer the same request differently (details/api-cli-mcp-contract.md
    section 1).
    """
    default = deep_budget() if depth is ContextDepth.DEEP else startup_budget()
    return ContextBudget(
        max_items=body.max_items or default.max_items,
        max_characters=body.max_characters or default.max_characters,
        max_full_documents=(
            body.max_full_documents
            if body.max_full_documents is not None
            else default.max_full_documents
        ),
        category_limits=(
            default.category_limits if body.category_limits is None else body.category_limits
        ),
        response_limit=default.response_limit,
    )


def _scope_request(body: ResolveScopeRequest) -> ScopeRequest:
    return ScopeRequest(
        cwd=PurePath(body.cwd) if body.cwd else None,
        repository_root=PurePath(body.repository_root) if body.repository_root else None,
        workspace_id=(
            _concept_id(body.workspace_id, field="workspace_id") if body.workspace_id else None
        ),
    )


def _not_mapped(workspace_id: str) -> _ApiError:
    return _ApiError(
        404,
        StructuredError(
            "not_mapped",
            f"workspace {workspace_id} is not mapped on this machine",
            {"workspace_id": workspace_id},
            repair_hint="map it with `never4ga workspace map <workspace-id>`",
        ),
    )


def _unavailable(capability: str) -> _ApiError:
    """The app was built without the machine-local state this needs.

    A stub answer would be worse: a client would take an empty registry for a
    machine with nothing mapped, which is a different fact entirely.
    """
    return _ApiError(
        503,
        StructuredError(
            "capability_unavailable",
            f"{capability} is not available on this service",
            {"capability": capability},
            retryable=False,
        ),
    )


def _validate(session: VaultSession, request: ValidateRequest) -> ValidateResponse:
    """core/02 section 23: validation always reports, and never repairs.

    The walk itself is `services.validate_vault`, so this interface and the CLI
    cannot answer the same question differently, for instance by one of them
    skipping a document whose frontmatter will not parse.
    """
    # Parsing each path here keeps a malformed one a 4xx from this interface
    # rather than an error raised inside the service.
    paths = [str(_vault_path(raw)) for raw in request.paths] if request.paths else None
    reports = validate_vault(session.documents, level=request.level, paths=paths)
    return ValidateResponse(
        checked=len(reports),
        valid=sum(1 for report in reports if report.ok),
        documents=[
            ValidatedDocumentModel(
                path=str(report.path),
                ok=report.ok,
                issues=[
                    ValidationIssueModel(
                        code=issue.code,
                        severity=issue.severity.value,
                        field=issue.field,
                        message=issue.message,
                    )
                    for issue in report.issues
                ],
            )
            for report in reports
            if report.issues
        ],
    )


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _concept_id(raw: str, *, field: str) -> ConceptId:
    try:
        return ConceptId.parse(raw)
    except IdentityError as error:
        raise _invalid_identity(raw, field, error) from error


def _vault_path(raw: str) -> VaultPath:
    try:
        return VaultPath.parse(raw)
    except VaultPathError as error:
        raise _bad_request("invalid_path", str(error), {"path": raw}) from error


# -- errors ---------------------------------------------------------------


class _ApiError(Exception):
    """An error already shaped for section 12, carrying its status code."""

    def __init__(self, status_code: int, error: StructuredError) -> None:
        super().__init__(error.message)
        self.status_code = status_code
        self.error = error


#: Which HTTP status each work refusal deserves, read or write. A policy
#: refusal is not a client error in the shape of the request -- the request was
#: well formed and the workspace does not permit it -- so 403 rather than 400 or
#: 422. A tracker that did not answer is the service's dependency being down, so
#: 503, as `ProviderUnavailableError` is everywhere else in this API. An item
#: the tracker answered that it does not have is 404, as `WorkItemNotFoundError`
#: is in `errors.py`. Anything absent is 400, which is right for
#: `unknown_work_item_status` and wrong for a code that describes the vault
#: rather than the request, so every such code has to be listed here.
_WRITE_STATUS: Final[Mapping[str, int]] = {
    "tracker_unreachable": 503,
    "work_item_not_found": 404,
    "scope_unresolved": 404,
    "path_not_visible": 404,
    "workspace_missing": 404,
    "work_management_undeclared": 409,
    "connection_undefined": 409,
    "connection_not_writable": 409,
    "connection_unreadable": 409,
    "write_not_permitted": 403,
    "sync_policy_declines": 403,
    "propose_failed": 422,
    "write_failed": 422,
}


def _write_status(error: StructuredError) -> int:
    return _WRITE_STATUS.get(error.code, 400)


def _invalid_identity(raw: str, field: str, error: Exception) -> _ApiError:
    return _ApiError(
        400,
        StructuredError(
            "invalid_identity",
            str(error),
            {"field": field, "value": raw},
            repair_hint="identity is a canonical UUIDv7 (core/02 section 5.1)",
        ),
    )


def _bad_request(code: str, message: str, details: dict[str, Any]) -> _ApiError:
    return _ApiError(400, StructuredError(code, message, details))


def _not_found(concept_id: str) -> _ApiError:
    return _ApiError(
        404,
        StructuredError(
            "concept_not_found",
            f"no concept with id {concept_id}",
            {"id": concept_id},
            repair_hint="search for it, or check that the vault has been indexed",
        ),
    )


#: What an HTTP status means when Starlette raises it rather than a route.
_HTTP_CODES: Final = {
    401: "unauthenticated",
    404: "not_found",
    405: "method_not_allowed",
}


def _install_error_handlers(app: FastAPI) -> None:
    """Render every failure in the section 12 shape.

    FastAPI's defaults produce ``{"detail": ...}``, which is a second error
    format for a client to learn. None of these handlers renders an exception's
    traceback or repeats what the caller sent, so a credential presented in a
    header cannot come back out in a body
    (`details/security-configuration.md` section 9).
    """

    def render(status_code: int, error: StructuredError) -> JSONResponse:
        headers = _CHALLENGE if status_code == 401 else None
        return JSONResponse(
            status_code=status_code,
            content=ErrorResponse.of(error).model_dump(),
            headers=headers,
        )

    @app.exception_handler(_UnauthenticatedError)
    async def unauthenticated(_: Request, __: Exception) -> JSONResponse:
        return render(
            401,
            StructuredError(
                "unauthenticated",
                "this endpoint requires the local API credential",
                repair_hint=(
                    "send `Authorization: Bearer <credential>`; the CLI reads it "
                    "from the local secret store"
                ),
            ),
        )

    @app.exception_handler(_ApiError)
    async def structured(_: Request, exception: Exception) -> JSONResponse:
        assert isinstance(exception, _ApiError)
        return render(exception.status_code, exception.error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exception: Exception) -> JSONResponse:
        assert isinstance(exception, RequestValidationError)
        return render(
            422,
            StructuredError(
                "invalid_request",
                "the request did not match this endpoint's schema",
                # Location and reason only. The offending *input* is left out
                # deliberately: echoing it back is how a secret in a body ends
                # up in a log.
                {
                    "issues": [
                        {"location": [str(part) for part in issue["loc"]], "message": issue["msg"]}
                        for issue in exception.errors()
                    ]
                },
                repair_hint="see /openapi.json for the expected shape",
            ),
        )

    @app.exception_handler(Never4gaError)
    async def never4ga_error(_: Request, exception: Exception) -> JSONResponse:
        """One handler, one table (`api/errors.py`).

        The table, not the Python class name, decides the status and code, so
        a class rename cannot change the wire contract.
        """
        assert isinstance(exception, Never4gaError)
        mapped = status_for(exception)
        # An exception may carry a hint for its own case; the table's hint is
        # the fallback for the rest. A generic hint that contradicts the
        # specific failure is worse than none, and the CLI makes the same
        # substitution so the two surfaces say one thing.
        hint = getattr(exception, "hint", None) or mapped.repair_hint
        return render(
            mapped.status,
            StructuredError(
                mapped.code,
                str(exception),
                retryable=mapped.retryable,
                repair_hint=hint,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exception: Exception) -> JSONResponse:
        assert isinstance(exception, StarletteHTTPException)
        status = exception.status_code
        return render(
            status,
            StructuredError(
                _HTTP_CODES.get(status, "http_error"),
                str(exception.detail),
                repair_hint="see /openapi.json for the endpoints this service exposes",
            ),
        )
