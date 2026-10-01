"""Request and response models for the local API.

These are pydantic models, which is why the API uses FastAPI rather than
Starlette alone: the generated OpenAPI document makes the surface inspectable
without reading the routes.

They stop here. core/05 section 15 keeps business logic out of the interfaces,
and a model that leaked below this layer would start competing with the frozen
dataclasses in ``never4ga.domain`` and ``never4ga.services`` for the role of
domain model -- at which point the API's shape drives the domain rather than
describing it. Every model here is built *from* a service result and never
passed back down.

Field names match what the CLI emits for the same data
(`details/api-cli-mcp-contract.md` section 4). A Skill that can read one should
not have to learn the other.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from never4ga.context.budget import REQUIRED_READING_CEILING
from never4ga.domain.context import BudgetUsage, ContextItem, ContextPack
from never4ga.domain.provenance import AcquisitionReason
from never4ga.domain.scope import WorkspaceMapping
from never4ga.domain.signals import ContextSignal
from never4ga.errors import StructuredError
from never4ga.schema import ValidationLevel
from never4ga.services import (
    ConceptView,
    Created,
    Diagnosis,
    IndexHealth,
    IndexRun,
    ScopeResolution,
    SearchResponse,
)
from never4ga.services.authoring import format_timestamp

__all__ = [
    "BudgetUsageModel",
    "ConceptAdoptRequest",
    "ConceptCreateRequest",
    "ConceptResponse",
    "ConceptWrittenResponse",
    "ContextItemModel",
    "ContextPackResponse",
    "ContextRequestModel",
    "ContextSignalModel",
    "DoctorResponse",
    "ErrorResponse",
    "ForeignRunModel",
    "HealthResponse",
    "IndexRequest",
    "IndexRunResponse",
    "ReasonModel",
    "ResolveScopeRequest",
    "ResolvedScopeModel",
    "SearchRequestModel",
    "SearchResponseModel",
    "ValidateRequest",
    "ValidateResponse",
    "VaultResponse",
    "WorkspaceListResponse",
    "WorkspaceMappingModel",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- requests -------------------------------------------------------------


class IndexRequest(_Model):
    changed_only: bool = Field(
        default=False,
        description="Skip documents whose content hash the index already holds.",
    )


class SearchRequestModel(_Model):
    query: str = Field(description='Words, "quoted phrases", or an identifier.')
    types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    workspace_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=200)
    explain: bool = Field(
        default=False,
        description=(
            "Include the fused score and the rank each lane gave a result. "
            "Unstable: this payload changes while the ranking is tuned."
        ),
    )


class ValidateRequest(_Model):
    paths: list[str] = Field(
        default_factory=list,
        description="Vault-relative paths. Empty validates every concept.",
    )
    level: ValidationLevel = ValidationLevel.STRICT


class ConceptCreateRequest(_Model):
    """POST /v1/concepts -- the same arguments `concept create` takes."""

    type: str
    title: str
    workspace: str | None = None
    in_directory: str | None = Field(
        default=None,
        alias="in",
        description="The folder to create it in, when the type's home is not meant.",
    )
    description: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    numbered: bool = Field(
        default=False,
        description=(
            "A decision only: number it where its folder has no numbered "
            "records yet. A folder that numbers them always does (core/02 section 21.11)."
        ),
    )
    series: str | None = Field(
        default=None,
        description="A decision only: which of its folder's ADR series it belongs to.",
    )
    actor: str | None = Field(
        default=None,
        description=(
            "Who generated.by records, as <producer>/<version>. core/02 "
            "section 5.2 makes naming it a MUST for an agent; absent means a "
            "person is writing."
        ),
    )


class ConceptAdoptRequest(_Model):
    """POST /v1/concepts/adopt -- the same arguments `adopt` takes."""

    path: str
    type: str | None = None
    in_directory: str | None = Field(
        default=None,
        alias="in",
        description=(
            "The folder a note in foreign material moves to (core/01 section 1). Refused "
            "for a file already inside a root, which stays where it sits."
        ),
    )
    fields: dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None


# -- responses ------------------------------------------------------------


class ErrorBody(_Model):
    """`details/api-cli-mcp-contract.md` section 12."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    repair_hint: str | None = None


class ErrorResponse(_Model):
    error: ErrorBody

    @classmethod
    def of(cls, error: StructuredError) -> ErrorResponse:
        return cls(error=ErrorBody(**error.as_dict()))


class HealthResponse(_Model):
    """Liveness, and nothing that needs a credential to know."""

    status: str
    service: str
    api_version: str
    version: str
    #: Which build is actually running. The version string is the same across
    #: every commit of a release line, so it cannot answer "is this service
    #: running the code I have?" -- and a client that delegates to a service
    #: running different code gets confident, wrong answers.
    build: str
    vault_id: str
    uptime_seconds: float


class IndexStatusModel(_Model):
    built: bool
    indexed_documents: int
    #: Foreign notes held by path (core/02 section 3.3), as `index --status`
    #: reports them.
    indexed_foreign: int
    last_indexed_at: str | None
    stale: bool

    @classmethod
    def of(cls, health: IndexHealth) -> IndexStatusModel:
        return cls(
            built=health.last_indexed_at is not None,
            indexed_documents=health.indexed_documents,
            indexed_foreign=health.indexed_foreign,
            last_indexed_at=health.last_indexed_at,
            stale=health.is_stale,
        )


class VaultResponse(_Model):
    vault_id: str
    title: str | None
    concept_count: int
    concepts_by_type: dict[str, int]
    index: IndexStatusModel


class IssueModel(_Model):
    code: str
    message: str


class ForeignRunModel(_Model):
    """Foreign notes a run indexed by path, counted apart from concepts."""

    indexed: int
    unchanged: int
    removed: int


class IndexRunResponse(_Model):
    indexed: int
    unchanged: int
    removed: int
    issues: list[IssueModel]
    foreign: ForeignRunModel

    @classmethod
    def of(cls, run: IndexRun) -> IndexRunResponse:
        return cls(
            indexed=run.indexed,
            unchanged=run.unchanged,
            removed=run.removed,
            issues=[IssueModel(code=issue.code, message=issue.message) for issue in run.issues],
            foreign=ForeignRunModel(
                indexed=run.foreign_indexed,
                unchanged=run.foreign_unchanged,
                removed=run.foreign_removed,
            ),
        )


class NeighborModel(_Model):
    id: str
    type: str


class RelationsModel(_Model):
    outgoing: list[NeighborModel]
    incoming: list[NeighborModel]


class ConceptWrittenResponse(_Model):
    """A concept a write endpoint produced, shaped like the CLI's payload."""

    id: str
    path: str
    created_directories: list[str] = Field(default_factory=list)
    created_files: list[str] = Field(default_factory=list)
    placement: str | None = None
    moved_from: str | None = Field(
        default=None,
        description="Where the file was, when adopting it moved it out of foreign material.",
    )
    set_aside: list[str] = Field(
        default_factory=list,
        description=(
            "The writer's keys kept under extensions.adopted because the concept "
            "does not carry them unchanged (core/02 section 3.3)."
        ),
    )

    @classmethod
    def of(cls, created: Created) -> ConceptWrittenResponse:
        return cls(
            id=str(created.concept_id),
            path=str(created.path),
            created_directories=[str(path) for path in created.directories],
            created_files=[str(path) for path in created.files],
            placement=created.placement_reason or None,
            moved_from=str(created.moved_from) if created.moved_from is not None else None,
            set_aside=list(created.set_aside),
        )


class ConceptResponse(_Model):
    id: str
    path: str
    frontmatter: dict[str, Any]
    body: str
    index_is_stale: bool
    relations: RelationsModel

    @classmethod
    def of(cls, view: ConceptView) -> ConceptResponse:
        return cls(
            id=str(view.document.concept_id),
            path=str(view.document.path),
            # Unknown extension fields ride through untouched: core/02
            # section 31 requires them to survive a round trip, and an
            # interface is part of that trip.
            frontmatter=dict(view.document.frontmatter),
            body=view.document.body,
            index_is_stale=view.index_is_stale,
            relations=RelationsModel(
                outgoing=[
                    NeighborModel(id=str(n.concept_id), type=n.relation_type) for n in view.outgoing
                ],
                incoming=[
                    NeighborModel(id=str(n.concept_id), type=n.relation_type) for n in view.incoming
                ],
            ),
        )


class SearchResultModel(_Model):
    explain: ExplainModel | None = None
    #: Null for a note in foreign material, which has no identity and is
    #: reached by ``path`` (core/02 section 3.3); so is ``type``.
    id: str | None
    path: str
    title: str
    type: str | None
    rank: int
    retriever: str
    reason: str
    heading_path: list[str]
    excerpt: str
    lines: list[int] | None
    authority: str | None
    status: str | None
    lifecycle: str | None
    is_stale: bool
    chunk_has_changed: bool


class ExplainModel(_Model):
    """Why a result ranked where it did.

    Explicitly unstable. It exists so "the embedder makes retrieval
    better" is a claim somebody can check, not as an interface to build on.
    """

    score: float
    lanes: dict[str, int]


class SearchResponseModel(_Model):
    index_is_stale: bool
    results: list[SearchResultModel]

    @classmethod
    def of(cls, response: SearchResponse, *, explain: bool = False) -> SearchResponseModel:
        return cls(
            index_is_stale=response.index_is_stale,
            results=[
                SearchResultModel(
                    id=None if result.concept_id is None else str(result.concept_id),
                    path=str(result.path),
                    title=result.title,
                    type=result.concept_type,
                    rank=result.rank,
                    retriever=result.retriever,
                    # core/07: every item says why it was acquired.
                    reason=result.reason.code,
                    heading_path=list(result.heading_path),
                    excerpt=result.excerpt,
                    lines=(
                        [result.line_range.start, result.line_range.end]
                        if result.line_range
                        else None
                    ),
                    authority=result.authority,
                    status=result.status,
                    lifecycle=result.lifecycle,
                    is_stale=result.is_stale,
                    chunk_has_changed=result.chunk_has_changed,
                    explain=(
                        ExplainModel(score=result.score, lanes=dict(result.lanes))
                        if explain
                        else None
                    ),
                )
                for result in response.results
            ],
        )


class ValidationIssueModel(_Model):
    code: str
    severity: str
    field: str | None
    message: str


class ValidatedDocumentModel(_Model):
    path: str
    ok: bool
    issues: list[ValidationIssueModel]


class ValidateResponse(_Model):
    checked: int
    valid: int
    documents: list[ValidatedDocumentModel]


class FindingModel(_Model):
    code: str
    severity: str
    message: str
    path: str | None
    repair_hint: str | None


class DoctorResponse(_Model):
    vault_id: str | None
    healthy: bool
    concept_count: int
    findings: list[FindingModel]

    @classmethod
    def of(cls, diagnosis: Diagnosis) -> DoctorResponse:
        return cls(
            vault_id=str(diagnosis.vault_id) if diagnosis.vault_id else None,
            healthy=diagnosis.healthy,
            concept_count=diagnosis.concept_count,
            findings=[
                FindingModel(
                    code=finding.code,
                    severity=finding.severity.value,
                    message=finding.message,
                    path=str(finding.path) if finding.path else None,
                    repair_hint=finding.repair_hint,
                )
                for finding in diagnosis.findings
            ],
        )


# -- workspaces and context ---------------------------------------------


class ResolveScopeRequest(_Model):
    """Where the caller is. All three are optional and tried most-explicit first."""

    cwd: str | None = Field(default=None, description="An absolute path on this machine.")
    repository_root: str | None = None
    workspace_id: str | None = None


class WorkWriteRequest(_Model):
    """A change to a work item, and whether to send it.

    ``apply`` is the HTTP form of the CLI's ``--apply``. It defaults to
    false, so a client that forgets it gets a description rather than a change
    to somebody's system of record.
    """

    cwd: str = Field(description="An absolute path in the workspace whose tracker to write to.")
    apply: bool = Field(default=False, description="Send it. Without this, only describe it.")
    session_id: str | None = Field(
        default=None,
        description=(
            "The session this write belongs to, so wrap sees it happened. "
            "Optional, as the CLI's --session is."
        ),
    )


class WorkUpdateRequest(WorkWriteRequest):
    work_item: str
    fields: dict[str, Any] = Field(default_factory=dict)


class WorkCreateRequest(WorkWriteRequest):
    title: str
    fields: dict[str, Any] = Field(default_factory=dict)


class WorkCommentRequest(WorkWriteRequest):
    work_item: str
    body: str
    amends: str | None = Field(
        default=None, description="An existing comment's id to replace rather than add to."
    )


class WorkItemWrittenModel(_Model):
    ref: str
    title: str
    status: str | None = None
    url: str | None = None


class WorkWriteResponse(_Model):
    """What a write verb did, or would do."""

    workspace: str
    connection: str
    action: str
    proposal: str = Field(description="What this would send, rendered for a person.")
    applied: bool
    noop: bool = False
    item: WorkItemWrittenModel | None = None
    activity: str | None = None
    record_lost: str | None = Field(
        default=None,
        description=(
            "Set when the write reached the tracker and could not be recorded against "
            "the session that made it. The write happened: do not retry it. `wrap` will "
            "be missing this observation."
        ),
    )


class WorkspaceMappingModel(_Model):
    workspace_id: str
    workspace_path: str
    repository_root: str | None
    parent_id: str | None

    @classmethod
    def of(cls, mapping: WorkspaceMapping) -> WorkspaceMappingModel:
        return cls(
            workspace_id=str(mapping.workspace_id),
            workspace_path=str(mapping.workspace_path),
            repository_root=(
                str(mapping.repository_root) if mapping.repository_root is not None else None
            ),
            parent_id=str(mapping.parent_id) if mapping.parent_id is not None else None,
        )


class WorkspaceListResponse(_Model):
    mappings: list[WorkspaceMappingModel]


class ReasonModel(_Model):
    """Why an item is here (core/07 section 12)."""

    stage: str
    code: str
    detail: str

    @classmethod
    def of(cls, reason: AcquisitionReason) -> ReasonModel:
        return cls(stage=str(reason.stage), code=str(reason.code), detail=reason.detail)


class ResolvedScopeModel(_Model):
    workspace_id: str
    workspace_path: str
    repository_root: str | None
    parent_chain: list[str]
    reason: ReasonModel
    conflicts: list[str]

    @classmethod
    def of(cls, resolution: ScopeResolution) -> ResolvedScopeModel:
        scope = resolution.scope
        return cls(
            workspace_id=str(scope.workspace_id),
            workspace_path=str(scope.workspace_path),
            repository_root=(
                str(scope.repository_root) if scope.repository_root is not None else None
            ),
            parent_chain=[str(parent) for parent in scope.parent_chain],
            reason=ReasonModel.of(scope.reason),
            conflicts=list(resolution.conflicts),
        )


class ContextRequestModel(_Model):
    """A context request carries no prompt and no model configuration.

    core/00 #27 keeps Never4gA out of the inference business, and a request
    body that accepted a prompt would be the first step into it.
    """

    scope: ResolveScopeRequest = Field(default_factory=ResolveScopeRequest)
    terms: list[str] = Field(default_factory=list)
    exact_identifiers: list[str] = Field(default_factory=list)
    #: One external work item to centre the pack on. A provider-side
    #: reference such as an OpenProject work-package id -- capped because a
    #: reference is short, and this is not where anything longer belongs.
    work_item: str | None = Field(default=None, max_length=128)
    #: Ask the tracker rather than whatever was cached.
    refresh: bool = False
    #: Which agent client is asking (core/04 section 16). A label, not a secret.
    client: str | None = Field(default=None, max_length=64)
    #: Who is asking, as `<producer>/<version>` (core/02 section 5.2). A
    #: startup opens a session, the session records its actor, and `wrap`
    #: resolves `generated.by` from it. Without this field every session the
    #: service answered would claim `human:owner` wrote it. Unset means the
    #: owner: a person typing at their own vault. The pattern refuses a blank
    #: the way the CLI refuses `--actor ""`, so a request body is not a way
    #: around that check.
    actor: str | None = Field(default=None, max_length=128, pattern=r"\S")
    #: A *short* task description, which becomes retrieval terms and goes no
    #: further. core/04 section 16: the full raw user prompt SHOULD NOT be
    #: persisted merely because it was used for retrieval, so the response says
    #: a task was given rather than what it said. The length cap is the same
    #: sentence read as a constraint: this field is not where a prompt goes.
    task: str | None = Field(default=None, max_length=280)
    #: Unset means the depth's own default -- a deep pack has more room than a
    #: startup one, and a caller that asks for depth without also restating a
    #: ceiling means the one that depth was given.
    max_items: int | None = Field(default=None, ge=1, le=500)
    max_characters: int | None = Field(default=None, ge=1, le=2_000_000)
    max_full_documents: int | None = Field(default=None, ge=0)
    #: Unset means the startup defaults (`context.budget.STARTUP_CATEGORY_LIMITS`).
    #: An explicit empty object means uncapped, which lets one lane take the
    #: whole pack, so it has to be asked for.
    category_limits: dict[str, int] | None = Field(default=None)


class ContextItemModel(_Model):
    #: Null for a note in foreign material, reached by ``path`` (core/02
    #: section 3.3).
    id: str | None
    path: str
    title: str
    category: str
    priority: int
    reason: ReasonModel
    is_reference: bool
    #: Why the budget carried this as a reference, if it did (core/07
    #: section 10).
    reference_reason: str | None = None
    #: Required reading, and what reading it costs (core/07 section 10).
    required: bool = False
    size: int | None = None
    #: What a standard says it governs, so a listed one can be judged without
    #: opening it (core/07 section 10).
    description: str | None = None
    #: Always null here: the service does not know where its caller sees the
    #: vault. The CLI and MCP clients fill it from their own root.
    disk_path: str | None = None
    #: core/02 section 15.2: the `stale_after` this document has passed, if any.
    is_stale: bool
    stale_since: str | None
    body: str | None

    @classmethod
    def of(cls, item: ContextItem) -> ContextItemModel:
        return cls(
            id=None if item.concept_id is None else str(item.concept_id),
            path=str(item.path),
            title=item.title,
            category=str(item.category),
            priority=item.priority,
            reason=ReasonModel.of(item.reason),
            is_reference=item.is_reference,
            reference_reason=(
                None if item.reference_reason is None else str(item.reference_reason)
            ),
            required=item.required,
            size=item.size,
            description=item.description,
            is_stale=item.is_stale,
            stale_since=(None if item.stale_since is None else format_timestamp(item.stale_since)),
            body=item.body,
        )


class ContextSignalModel(_Model):
    provider: str
    kind: str
    value: Any
    reason: ReasonModel

    @classmethod
    def of(cls, signal: ContextSignal) -> ContextSignalModel:
        return cls(
            provider=signal.provider_id,
            kind=signal.kind,
            value=signal.value,
            reason=ReasonModel.of(signal.reason),
        )


class BudgetUsageModel(_Model):
    items: int
    characters: int
    estimated_tokens: int
    full_documents: int
    dropped_items: int
    #: The pack's whole required reading, and the ceiling it is watched
    #: against (core/07 section 10).
    required_characters: int = 0
    required_ceiling: int = REQUIRED_READING_CEILING

    @classmethod
    def of(cls, usage: BudgetUsage) -> BudgetUsageModel:
        return cls(
            items=usage.items,
            characters=usage.characters,
            estimated_tokens=usage.estimated_tokens,
            full_documents=usage.full_documents,
            dropped_items=usage.dropped_items,
            required_characters=usage.required_characters,
        )


class ContextPackResponse(_Model):
    depth: str
    scope: ResolvedScopeModel
    items: list[ContextItemModel]
    signals: list[ContextSignalModel]
    usage: BudgetUsageModel
    degraded_providers: list[str]
    llm_stages: list[str]
    #: Who asked, and whether a task was named -- never what the task said.
    client: str | None = None
    task_given: bool = False
    #: The session a startup opened. Absent on focused and deep retrieval,
    #: which happen inside a session rather than beginning one.
    session_id: str | None = None

    @classmethod
    def of(cls, depth: str, pack: ContextPack, resolution: ScopeResolution) -> ContextPackResponse:
        return cls(
            depth=depth,
            scope=ResolvedScopeModel.of(resolution),
            items=[ContextItemModel.of(item) for item in pack.items],
            signals=[ContextSignalModel.of(signal) for signal in pack.signals],
            usage=BudgetUsageModel.of(pack.usage),
            degraded_providers=list(pack.degraded_providers),
            llm_stages=list(pack.llm_stages),
            client=pack.client,
            task_given=pack.task_given,
            session_id=None if pack.session is None else str(pack.session),
        )
