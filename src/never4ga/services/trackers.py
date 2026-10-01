"""Read-through caching over a work-management provider.

`details/external-work-management-architecture.md` Stage 2: "Never4gA maintains
a derived local cache/index for faster context/search. OpenProject remains
authoritative."

This is a decorator rather than a service with its own vocabulary, so that
everything above it keeps seeing a
:class:`~never4ga.ports.work_management.WorkManagementProvider` and nothing has
to know whether an answer was cached. It is written against ports alone -- the
provider it wraps may be OpenProject, a fake, or the second adapter
`details/openproject-adapter.md` section 14 anticipates.

Three rules decide what is cached and when it is trusted.

**A key may be answered from the cache; a query may not.** `details/openproject-adapter.md`
section 9 is about an explicit ticket read -- "use fresh cache if policy
permits; otherwise refresh" -- and a ticket is a key. A search asks what is true
*now*, and there is no honest way to answer that from what was true earlier, so
:meth:`search_work_items` always asks the tracker and stores what comes back.

**A failed read fails.** `core/05` section 19 wants Never4gA useful with a
provider unreachable, and quietly serving a stale ticket in place of a live one
is not that: an empty or old answer from a work tracker reads as "no open work",
which is a sentence nobody said. What is cached stays readable through
:meth:`cached_items`, which hands back ``fetched_at`` with every entry so a
caller cannot forget when it was true.

**A miss is not cached.** A ticket absent now may exist in a minute, and a
remembered "no" would outlast the fact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ExternalId, WorkItemKey
from never4ga.ports.tracker_cache import CachedWorkItem, TrackerCache
from never4ga.ports.work_management import (
    ProviderHealth,
    WorkItem,
    WorkItemQuery,
    WorkManagementProvider,
)

__all__ = [
    "DEFAULT_MAX_AGE",
    "CachingWorkManagementProvider",
    "RefreshReport",
    "reference_key",
]


def reference_key(connection: str, project_ref: str, ref: ExternalId) -> WorkItemKey:
    """Where a targeted read looks for one item.

    Module level because the *write* path has to forget exactly what the read
    path would look up, and a second hand-built key is how an invalidation
    silently misses: it would forget a row nobody reads and leave the stale
    one answering.

    ``project_ref`` is the connection's rather than the workspace's. A
    workspace may declare a different one, and :meth:`get_work_item` does not
    consult it -- so this is the key that exists, not the one that arguably
    ought to.
    """
    return WorkItemKey(connection=connection, project_ref=project_ref, external_id=ref)


#: How long a cached ticket answers for before it is fetched again.
#:
#: A constant rather than configuration: it becomes a setting when somebody has
#: a reason to change it. Short enough that a status change
#: shows up within a working rhythm, long enough that reading the same ticket
#: twice in a session costs one request.
DEFAULT_MAX_AGE: Final = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """What an explicit refresh did."""

    fetched: int
    forgotten: int
    at: datetime


class CachingWorkManagementProvider:
    """A provider that remembers what it was told, and says when it was told."""

    def __init__(
        self,
        provider: WorkManagementProvider,
        *,
        cache: TrackerCache,
        connection: str,
        project_ref: str = "",
        max_age: timedelta = DEFAULT_MAX_AGE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._connection = connection
        self._project_ref = project_ref
        self._max_age = max_age
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def provider_id(self) -> str:
        """The wrapped provider's. Caching does not make a new tracker."""
        return self._provider.provider_id

    @property
    def capabilities(self) -> frozenset[WorkManagementCapability]:
        return self._provider.capabilities

    def health(self) -> ProviderHealth:
        return self._provider.health()

    def get_work_item(self, ref: ExternalId, *, refresh: bool = False) -> WorkItem | None:
        """One work item, from the cache when it is fresh enough.

        ``refresh`` is what the surfaces' ``--refresh`` reaches: it asks the
        tracker whatever the cache holds.
        """
        if not refresh:
            cached = self._cache.get(self._reference_key(ref))
            if cached is not None and cached.age(self._clock()) < self._max_age.total_seconds():
                return cached.item
        found = self._provider.get_work_item(ref)
        if found is None:
            return None
        self._cache.put_many(self._entries([found]))
        return found

    def search_work_items(self, query: WorkItemQuery) -> Sequence[WorkItem]:
        """Always the tracker, and everything it says is cached on the way past.

        A complete answer also prunes. This is the path ordinary reads take, so
        forgetting only in `refresh` would let the cache grow without bound.
        """
        self._store(found := self._provider.search_work_items(query), query=query)
        return found

    # -- beyond the port --------------------------------------------------

    def cached_items(self) -> Sequence[CachedWorkItem]:
        """What is cached for this connection and project, with its age.

        Reads nothing and asks nothing: this is the offline view, and every
        entry carries ``fetched_at`` so it cannot be mistaken for a live one.
        """
        return self._cache.entries(self._connection, self._project_ref)

    def refresh(self, query: WorkItemQuery | None = None) -> RefreshReport:
        """Fetch and store, then forget what the tracker no longer returns.

        The forgetting matters: a ticket moved to another project would
        otherwise stay cached for ever, and a derived view (`core/03` section
        20) would keep showing work that is not there.

        It only happens when the answer was a *complete* listing of this
        project. A narrowed refresh -- one status, one assignee, a limit --
        never asked about the rest, and reading "not in this answer" as "gone"
        would empty the cache a slice at a time.

        A refresh that cannot reach the tracker raises and changes nothing.
        """
        asked = query or WorkItemQuery()
        found = self._provider.search_work_items(asked)
        at = self._clock()
        entries, gone = self._store(found, query=asked, at=at)
        return RefreshReport(fetched=len(entries), forgotten=len(gone), at=at)

    def _store(
        self,
        items: Sequence[WorkItem],
        *,
        query: WorkItemQuery,
        at: datetime | None = None,
    ) -> tuple[list[CachedWorkItem], list[WorkItemKey]]:
        """Cache one answer, and drop what a complete one proves is gone.

        Shared by `search_work_items` and `refresh` so the two cannot disagree
        about when absence is evidence.
        """
        entries = self._entries(items, at=at)
        self._cache.put_many(entries)
        gone: list[WorkItemKey] = []
        if _is_complete_listing(query, project_ref=self._project_ref, returned=len(entries)):
            current = {entry.key for entry in entries}
            gone = [entry.key for entry in self.cached_items() if entry.key not in current]
            for key in gone:
                self._cache.forget(key)
        return entries, gone

    # -- internals --------------------------------------------------------

    def _reference_key(self, ref: ExternalId) -> WorkItemKey:
        """Where this instance looks for one item: its own connection and project."""
        return reference_key(self._connection, self._project_ref, ref)

    def _key(self, item: WorkItem) -> WorkItemKey:
        """Where this item belongs, which is not always where this instance looks.

        A search may name a project other than the configured one, and the item
        knows which project it came from -- `normalise` puts core/03 section
        17's three terms on every item it produces. Filing those results under
        the configured project would build a cache that answers questions about
        the wrong tracker.
        """
        return WorkItemKey(
            connection=self._connection,
            project_ref=str(item.extra.get("project_ref") or self._project_ref),
            external_id=item.ref,
        )

    def _entries(
        self, items: Sequence[WorkItem], *, at: datetime | None = None
    ) -> list[CachedWorkItem]:
        """One answer arrived at one moment, so it gets one fetch time.

        Reading the clock per item would give a single response several ages,
        and the last item of a long page would look fresher than the first.
        """
        fetched_at = at or self._clock()
        return [
            CachedWorkItem(key=self._key(item), item=item, fetched_at=fetched_at) for item in items
        ]


def _is_complete_listing(query: WorkItemQuery, *, project_ref: str, returned: int) -> bool:
    """Whether this query saw everything in this project.

    Only a complete listing licenses forgetting. A filter or another project
    answers a different question, and absence from it is evidence of nothing.

    A cap is different from a filter. Asking for at most 200 and being handed
    78 means the tracker had no more to give: nothing can have been missed by a
    request that came back under its own ceiling. Every caller passes a cap,
    so treating any cap as disqualifying would mean nothing is ever forgotten
    and deleted items accumulate indefinitely.

    A listing that came back *full* is still inconclusive: the tracker may have
    had more, and the items beyond the ceiling were never asked about.
    """
    if query.project is not None and query.project != project_ref:
        return False
    if query.statuses or query.assignees or query.terms or query.updated_since:
        return False
    return query.limit is None or returned < query.limit
