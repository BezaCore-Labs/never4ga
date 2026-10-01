"""Tracker state as Context Pack signals (`core/07` Stage F).

Stage F's rule is the whole design: "use provider API responses rather than
asking an LLM to infer ticket state." Everything here is a count, a status or an
identifier read from an API, and `domain/signals.py` refuses anything that is
not.

**A workspace has to have asked for this.** `core/03` section 15 declares a
tracker on the workspace, and each ``sync_policy`` says how far Never4gA may go
on its own: ``reference`` "does not automatically ingest/synchronize ticket
data", and a startup pack is about as automatic as it gets. So a workspace with
no declaration, with ``mode: native``, or with ``sync_policy: reference`` costs
one read of its own manifest and nothing else -- no connection resolved, no
provider built, no request made.

`core/07` section 4 fixes the interface as ``supports(request)`` and
``collect(request, resolved_scope)``, so the workspace -- which arrives with the
scope -- cannot be consulted in :meth:`supports`. The declining therefore
happens first thing in :meth:`collect`, so no cost is paid for a workspace that
has no tracker.

**Nothing depends on it.** An unreachable tracker raises, the assembler records
the provider as degraded, and the pack assembles without it (`core/05` section
19). What is cached answers in its place when there is anything cached -- dated,
and flagged as not live, because a stale answer passed off as a current one is
worse than no answer at all.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from never4ga.domain.connections import Connection
from never4ga.domain.context import ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ExternalId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope
from never4ga.domain.signals import MAX_SIGNAL_TEXT_LENGTH, ContextSignal
from never4ga.domain.work_policy import (
    DEFAULT_POLICY,
    EXTERNAL_MODE,
    INGESTING_POLICIES,
    WORK_MANAGEMENT_KEY,
    WRITING_POLICIES,
)
from never4ga.errors import (
    ConnectionDefinitionError,
    ProviderUnavailableError,
    WorkItemNotFoundError,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.tracker_cache import CachedWorkItem, TrackerCache
from never4ga.ports.work_management import WorkItem, WorkItemQuery, WorkManagementProvider
from never4ga.services.connections import ConnectionRegistry, project_ref_for

__all__ = ["ITEMS_BY_DEPTH", "WorkManagementProviderFactory", "WorkManagementSignalProvider"]

# `core/03` section 15's vocabulary lives in `domain/work_policy.py`, where the
# write half reads it too. One declaration, read by both halves: a read path and
# a write path that disagreed about what `read_write` means would disagree
# somewhere nobody was looking.

#: How many items each depth lists. Counts always cover everything that was
#: read: "five of forty" is a different fact from "five", and trimming the list
#: must not trim the summary.
ITEMS_BY_DEPTH: Final[Mapping[ContextDepth, int]] = {
    ContextDepth.STARTUP: 5,
    ContextDepth.FOCUSED: 10,
    ContextDepth.DEEP: 25,
}

#: How many items the counts are taken over. Bounded, because `core/07` section
#: 15 requires startup to stay bounded.
#:
#: This bounds the *read*, not the pack: `ITEMS_BY_DEPTH` above decides how many
#: reach a reader, and it is far smaller. Raising this therefore costs pages
#: from the tracker, not context -- and only for a project that actually has
#: them, since pages are fetched on demand.
#:
#: Reaching the ceiling sets `work.truncated`. Without it, a count that stopped
#: at the ceiling would look like a real total.
MAX_ITEMS_READ: Final = 1000

#: A provider is built from a connection by whoever picked the adapter. This
#: module never names one: `details/openproject-adapter.md` section 1 keeps
#: provider vocabulary inside its adapter, and the composition root is the only
#: place that knows which adapter a `provider` field means.
type WorkManagementProviderFactory = Callable[[Connection], WorkManagementProvider | None]


class WorkManagementSignalProvider:
    """What the tracker says about this workspace, as structured facts."""

    provider_id = "work_management"

    def __init__(
        self,
        *,
        documents: DocumentStore,
        connections: ConnectionRegistry,
        factory: WorkManagementProviderFactory,
        cache: TrackerCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._documents = documents
        self._connections = connections
        self._factory = factory
        self._cache = cache
        self._clock = clock or (lambda: datetime.now(UTC))

    def supports(self, request: ContextRequest) -> bool:
        """Every depth wants to know what is open.

        Whether *this workspace* has a tracker cannot be answered here: the
        interface `core/07` section 4 fixes gives :meth:`supports` the request
        and not the scope. :meth:`collect` declines instead, before any cost.
        """
        return True

    def collect(self, request: ContextRequest, scope: ResolvedScope) -> Sequence[ContextSignal]:
        declaration = self._declaration(scope.workspace_path)
        if declaration is None:
            return ()
        connection = self._connection(declaration)
        if connection is None:
            return ()
        provider = self._factory(connection)
        if provider is None:
            return ()

        policy = _declared_policy(declaration)
        # The *workspace's* project, not the connection's. One connection serves
        # every workspace on this machine, so taking it from the connection would
        # make every pack report the connection's default project.
        project_ref = project_ref_for(declaration, connection)
        try:
            items = list(
                provider.search_work_items(
                    WorkItemQuery(project=project_ref or None, limit=MAX_ITEMS_READ)
                )
            )
            current = self._current(provider, request)
        except ProviderUnavailableError as unreachable:
            return self._from_cache(request, connection, project_ref, policy, unreachable)
        return self._signals(
            request,
            connection=connection,
            project_ref=project_ref,
            policy=policy,
            items=items,
            as_of=self._clock(),
            live=True,
            current=current,
        )

    def _current(
        self, provider: WorkManagementProvider, request: ContextRequest
    ) -> WorkItem | None:
        """The one work item a request centred itself on, if it named one.

        The reference travels on the request rather than through a second path
        in the assembler, so this is the whole of `--ticket`: one extra read, at
        whatever depth was asked for.

        `refresh` only reaches a provider that offers it -- the port's
        `get_work_item` takes no such argument, and only the caching decorator
        adds one. A provider without a cache is already as fresh as it gets.
        """
        if request.work_item is None:
            return None
        reference = ExternalId(provider=provider.provider_id, value=request.work_item)
        found = provider.get_work_item(reference, refresh=request.refresh)
        if found is None:
            # The tracker answered and does not have it. That is a fault in the
            # reference rather than in the tracker, so the provider is degraded
            # rather than the pack failing.
            raise WorkItemNotFoundError(
                f"{provider.provider_id} has no work item {request.work_item!r}"
            )
        return found

    # -- deciding whether to read at all ----------------------------------

    def _declaration(self, workspace_path: VaultPath) -> Mapping[str, Any] | None:
        """`core/03` section 15's declaration, if this workspace made one.

        Read from the canonical store rather than the index, for the reason
        `ConnectionRegistry` reads it there: this is a configuration question,
        and an answer that changed because nobody had re-indexed would be wrong
        in a way that is hard to notice.
        """
        document = self._documents.get_by_path(workspace_path)
        if document is None:
            return None
        declared = document.frontmatter.get(WORK_MANAGEMENT_KEY)
        if not isinstance(declared, Mapping):
            return None
        if str(declared.get("mode", "")).strip() != EXTERNAL_MODE:
            return None
        # `sync_policy` is optional; core/03 section 15 lists `reference` first
        # and calls read the safe default for a first implementation, so an
        # undeclared policy is treated as `read` rather than as permission to
        # do more.
        policy = _declared_policy(declared)
        if policy not in INGESTING_POLICIES:
            return None
        return declared

    def _connection(self, declaration: Mapping[str, Any]) -> Connection | None:
        name = str(declaration.get("connection", "")).strip()
        if not name:
            return None
        try:
            return self._connections.resolve(name)
        except ConnectionDefinitionError:
            # A workspace naming a connection nobody wrote down is a
            # configuration fault, and `doctor` is where a fault is reported. A
            # startup pack says less rather than failing.
            return None

    # -- signals ----------------------------------------------------------

    def _from_cache(
        self,
        request: ContextRequest,
        connection: Connection,
        project_ref: str,
        policy: str,
        unreachable: ProviderUnavailableError,
    ) -> Sequence[ContextSignal]:
        """What was true last time, dated, or nothing at all.

        Re-raising on an empty cache is the point: nothing cached and nothing
        reachable is not "no open work", and the assembler's degraded-provider
        list is the honest way to say so.
        """
        entries: Sequence[CachedWorkItem] = (
            self._cache.entries(connection.name, project_ref) if self._cache is not None else ()
        )
        if not entries:
            raise unreachable
        return self._signals(
            request,
            connection=connection,
            project_ref=project_ref,
            policy=policy,
            items=[entry.item for entry in entries],
            as_of=max(entry.fetched_at for entry in entries),
            live=False,
        )

    def _signals(
        self,
        request: ContextRequest,
        *,
        connection: Connection,
        project_ref: str,
        policy: str,
        items: Sequence[WorkItem],
        as_of: datetime,
        live: bool,
        current: WorkItem | None = None,
    ) -> tuple[ContextSignal, ...]:
        listed = sorted(items, key=_changed_at, reverse=True)[: _limit(request.depth)]
        by_status: dict[str, int] = {}
        for item in items:
            by_status[item.status or "unknown"] = by_status.get(item.status or "unknown", 0) + 1

        emitted: list[tuple[str, Any]] = [
            ("work.connection", connection.name),
            ("work.project", project_ref),
            # What the workspace permits, beside what the tracker holds.
            # `sync_policy` decides whether writing is possible at all. An agent
            # reading a list of open items has no other way to know whether it
            # may touch them, and learning it only when a write is refused is
            # too late.
            #
            # Both, not one: "you may not" and "you may not *because the
            # workspace says read*" send a reader to different places.
            ("work.writable", policy in WRITING_POLICIES),
            ("work.sync_policy", policy),
            ("work.live", live),
            ("work.as_of", as_of.isoformat()),
            ("work.counted", len(items)),
            ("work.by_status", dict(sorted(by_status.items()))),
            ("work.items", [_summarise(item) for item in listed]),
        ]
        if live and len(items) >= MAX_ITEMS_READ:
            # The read filled its ceiling, so `work.counted` is a floor and the
            # status breakdown is taken over a slice. Saying so is the whole
            # point of a bound being "visible rather than silent" -- without
            # this, a truncated count is indistinguishable from a true one.
            emitted.append(("work.truncated", True))
        if current is not None:
            emitted.append(("work.current", _summarise(current, relations="full")))
        return tuple(self._signal(kind, value) for kind, value in emitted)

    def _signal(self, kind: str, value: Any) -> ContextSignal:
        return ContextSignal(
            provider_id=self.provider_id,
            kind=kind,
            value=value,
            reason=AcquisitionReason.of(ReasonCode.WORK_ITEM_CURRENT, detail=kind),
        )


def _limit(depth: ContextDepth) -> int:
    return ITEMS_BY_DEPTH.get(depth, ITEMS_BY_DEPTH[ContextDepth.STARTUP])


def _changed_at(item: WorkItem) -> datetime:
    """When the tracker last changed this, or the beginning of time.

    An item with no ``updated_at`` sorts last rather than crashing the sort:
    `details/openproject-adapter.md` section 8 allows any normalised field to
    be absent.
    """
    return item.updated_at or datetime.min.replace(tzinfo=UTC)


def _summarise(item: WorkItem, *, relations: str = "count") -> dict[str, Any]:
    """One work item as `core/07` Stage F's fields, and nothing else.

    A listed item says *how many* relations it has. Stage F asks for
    relationships, and a pack wants to know a ticket has three rather than which
    three -- a list of identifiers at startup spends the budget on what nobody
    has asked about yet. The item a request centred itself on is the exception:
    which three is exactly the question `--ticket` was asked.
    """
    summary: dict[str, Any] = {"id": item.ref.value, "title": _clip(item.title)}
    optional = (
        ("status", item.status),
        ("assignee", item.assignee),
        ("priority", item.priority),
        ("milestone", item.milestone),
        ("updated_at", item.updated_at.isoformat() if item.updated_at else None),
        ("url", item.url),
    )
    summary.update({name: value for name, value in optional if value})
    if item.relations:
        summary["relations"] = (
            [reference.value for reference in item.relations]
            if relations == "full"
            else len(item.relations)
        )
    return summary


def _clip(text: str) -> str:
    """Signals are short by contract; a long subject is truncated, not dropped."""
    if len(text) <= MAX_SIGNAL_TEXT_LENGTH:
        return text
    return text[: MAX_SIGNAL_TEXT_LENGTH - 1] + "…"


def _declared_policy(declaration: Mapping[str, Any]) -> str:
    """The workspace's `sync_policy`, normalised, with its documented default.

    The same expression `_declaration` uses to decide whether to ingest at all.
    Stated once here so the signal cannot drift from the gate: a pack that
    promised what the write path refuses would be worse than one that stayed
    quiet.
    """
    return str(declaration.get("sync_policy", DEFAULT_POLICY)).strip() or DEFAULT_POLICY
