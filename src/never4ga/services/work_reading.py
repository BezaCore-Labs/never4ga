"""Reading work items directly, rather than as Context Pack signals.

A startup pack carries tracker state, which is the mechanical answer to "what
is open here". This is the way to *ask*. `details/api-cli-mcp-contract.md`
section 5 lists ``never4ga_work_search`` and ``never4ga_work_get`` among the
read tools, and exposing them over MCP alone would make one interface able to
answer a question the others cannot, which is the asymmetry `core/05` section 13
exists to prevent.

So this is a service, and both surfaces call it. The shape mirrors
`work_writing.py` deliberately -- resolve where the caller is, read the
workspace's declaration, build a provider, ask -- because a reader and a writer
that discovered their tracker differently would eventually disagree about which
one they had.

**A read needs no policy.** ``sync_policy: read`` is enough, which is what
`work_signals.py` already treats as permission to query. Only ``reference``
declines, and it declines by saying so rather than by returning nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePath
from typing import Any

from never4ga.domain.connections import Connection
from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import ExternalId
from never4ga.domain.scope import ScopeRequest
from never4ga.domain.work_policy import (
    INGESTING_POLICIES,
    WORK_MANAGEMENT_KEY,
    WritePolicy,
)
from never4ga.errors import (
    ConnectionDefinitionError,
    Never4gaError,
    RepositoryMarkerError,
    ScopeResolutionError,
    StructuredError,
    UnknownWorkItemStatusError,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.work_management import WorkItem, WorkItemQuery, WorkManagementProvider
from never4ga.services.connections import ConnectionRegistry, project_ref_for
from never4ga.services.workspaces import WorkspaceService, scope_refusal

__all__ = ["BoundReader", "ProviderFactory", "WorkReadService", "tracker_unreachable"]

type ProviderFactory = Callable[[Connection], WorkManagementProvider | None]

#: How many items a search returns when the caller does not say. Small on
#: purpose: this answers a question somebody asked, and a hundred tickets is not
#: an answer.
DEFAULT_LIMIT = 20


@dataclass(frozen=True, slots=True)
class BoundReader:
    """A workspace, its tracker, and the project it names."""

    workspace: StoredDocument
    connection: Connection
    provider: WorkManagementProvider
    project: str


class WorkReadService:
    """Ask a workspace's tracker directly."""

    def __init__(
        self,
        *,
        documents: DocumentStore,
        workspaces: WorkspaceService,
        factory: ProviderFactory,
    ) -> None:
        self._documents = documents
        self._workspaces = workspaces
        self._factory = factory

    def bind(self, where: PurePath) -> BoundReader | StructuredError:
        try:
            resolution = self._workspaces.resolve(ScopeRequest(cwd=where))
        except (ScopeResolutionError, RepositoryMarkerError) as error:
            return scope_refusal(error, where)
        manifest = self._documents.get(resolution.scope.workspace_id)
        if manifest is None:
            return StructuredError(
                "workspace_missing",
                f"the resolved workspace {resolution.scope.workspace_id} is not in the vault",
                {"workspace": str(resolution.scope.workspace_id)},
            )

        declared = manifest.frontmatter.get(WORK_MANAGEMENT_KEY)
        policy = WritePolicy.from_declaration(declared if isinstance(declared, Mapping) else None)
        if not policy.has_external_tracker:
            return StructuredError(
                "work_management_undeclared",
                f"workspace {manifest.frontmatter.get('title')!r} declares no external tracker",
                {"workspace": str(manifest.concept_id), "mode": policy.mode or "none"},
                repair_hint="link one with `never4ga workspace link <connection>`",
            )
        if policy.sync_policy.strip() not in INGESTING_POLICIES:
            # `reference` knows the mapping and ingests nothing. Saying so beats
            # returning an empty list, which reads as "no open work".
            return StructuredError(
                "sync_policy_declines",
                f"workspace declares sync_policy {policy.sync_policy.strip()!r}, "
                "which knows the mapping and does not query the tracker",
                {"sync_policy": policy.sync_policy.strip()},
                repair_hint="set `work_management.sync_policy: read` on the workspace",
            )

        name = str(declared.get("connection", "")) if isinstance(declared, Mapping) else ""
        try:
            connection = ConnectionRegistry(documents=self._documents).resolve(name)
        except ConnectionDefinitionError as error:
            return StructuredError(
                "connection_undefined",
                str(error),
                {"connection": name},
                repair_hint="define it as an `integration` concept in 50_System/Integrations/",
            )
        provider = self._factory(connection)
        if provider is None:
            return StructuredError(
                "connection_unreadable",
                f"no adapter for connection {connection.name!r} on this machine",
                {"connection": connection.name, "provider": connection.provider},
                repair_hint=f"store a token with `never4ga connection set-token {connection.name}`",
            )
        reference = project_ref_for(declared if isinstance(declared, Mapping) else None, connection)
        return BoundReader(
            workspace=manifest,
            connection=connection,
            provider=provider,
            project=reference,
        )

    def search(
        self,
        bound: BoundReader,
        *,
        terms: Sequence[str] = (),
        statuses: Sequence[str] = (),
        limit: int | None = None,
    ) -> dict[str, Any] | StructuredError:
        query = WorkItemQuery(
            project=bound.project or None,
            terms=tuple(terms),
            statuses=tuple(statuses),
            limit=limit or DEFAULT_LIMIT,
        )
        try:
            found = bound.provider.search_work_items(query)
        except UnknownWorkItemStatusError as error:
            return _unknown_status(bound, error)
        except Never4gaError as error:
            return _unreachable(bound, error)
        return {
            "connection": bound.connection.name,
            "project_ref": bound.project,
            "counted": len(found),
            "items": [_item(one, listed=True) for one in found],
        }

    def get(
        self, bound: BoundReader, reference: str, *, refresh: bool = False
    ) -> dict[str, Any] | StructuredError:
        identity = ExternalId(provider=bound.provider.provider_id, value=reference)
        try:
            found = bound.provider.get_work_item(identity, refresh=refresh)
        except Never4gaError as error:
            return _unreachable(bound, error)
        if found is None:
            return StructuredError(
                "work_item_not_found",
                f"{bound.provider.provider_id} has no work item {reference!r}",
                {"work_item": reference, "connection": bound.connection.name},
            )
        return {
            "connection": bound.connection.name,
            "project_ref": bound.project,
            "item": _item(found),
        }


def _unknown_status(bound: BoundReader, error: UnknownWorkItemStatusError) -> StructuredError:
    """A status the tracker does not define is a typo, not an empty backlog.

    Reported separately from `tracker_unreachable` because the two send a
    caller somewhere different: one to their own spelling, the other to
    `connection health`. Without it, `--status open` against an instance whose
    statuses are New, Closed and Rejected would print `no work items`.
    """
    known = ", ".join(error.known)
    return StructuredError(
        "unknown_work_item_status",
        str(error),
        {
            "connection": bound.connection.name,
            "unknown": list(error.unknown),
            "known": list(error.known),
        },
        repair_hint=f"the tracker defines {known}" if known else "the tracker defines no statuses",
    )


def _unreachable(bound: BoundReader, error: Never4gaError) -> StructuredError:
    """A tracker that could not be asked is not a tracker with nothing in it.

    `core/05` section 19: an optional provider degrades rather than lying, and
    an empty answer from a work tracker reads as "no open work" -- a sentence
    nobody said.
    """
    return tracker_unreachable(bound.connection.name, error)


def tracker_unreachable(
    connection: str, error: Never4gaError, details: Mapping[str, Any] | None = None
) -> StructuredError:
    """The one way a tracker that did not answer is reported, read or write.

    Retryable, because the machine may merely be switched off. The write verbs
    use it too: a refusal is the one answer a caller should not retry, so an
    outage must not look like one.
    """
    return StructuredError(
        "tracker_unreachable",
        str(error),
        {"connection": connection, **(details or {})},
        retryable=True,
        repair_hint=f"check it with `never4ga connection health {connection}`",
    )


def _scalar(value: Any) -> Any:
    """A normalised value in a shape JSON can carry.

    The adapter normalises into Python types -- `datetime`, `date`,
    `ExternalId` -- because that is what a caller in process wants. This layer
    renders, and the rendering is JSON.
    """
    if isinstance(value, ExternalId):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _item(item: WorkItem, *, listed: bool = False) -> dict[str, Any]:
    """One work item, normalised. `details/openproject-adapter.md` section 8.

    Every field section 8 names is rendered. The adapter puts everything
    without a home on :class:`WorkItem` into ``extra``, so this reads it from
    there. ``description_excerpt`` in particular is what lets a backlog be read
    and not only listed.

    ``connection`` and ``project_ref`` stay out of the item on purpose: the
    response envelope already carries them, and a copy per item is a second home
    for one fact. ``project`` is a different fact -- the project the item is
    *in*, where ``project_ref`` is the one that was asked.
    """
    extra = item.extra
    fields: dict[str, Any] = {
        "ref": item.ref.value,
        "provider": item.ref.provider,
        "display_id": extra.get("display_id"),
        "project": extra.get("project"),
        "title": item.title,
        # A list carries the excerpt alone: fifty results must not mean fifty
        # full descriptions crossing the wire to be summarised in one line.
        "description": None if listed else extra.get("description"),
        "description_excerpt": extra.get("description_excerpt"),
        "type": extra.get("type"),
        "status": item.status,
        "priority": item.priority,
        "assignee": item.assignee,
        "responsible": extra.get("responsible"),
        "category": extra.get("category"),
        "created_at": extra.get("created_at"),
        "updated_at": item.updated_at,
        "start_date": extra.get("start_date"),
        "due_date": extra.get("due_date"),
        "percent_complete": extra.get("percent_complete"),
        "parent": extra.get("parent"),
        "relations": [one.value for one in item.relations],
        "relation_kinds": extra.get("relation_kinds", {}),
        "milestone": item.milestone,
        "sprint": extra.get("sprint"),
        "url": item.url,
        "provider_version": extra.get("provider_version"),
        "extensions": extra.get("extensions", {}),
    }
    return {name: _scalar(value) for name, value in fields.items()}
