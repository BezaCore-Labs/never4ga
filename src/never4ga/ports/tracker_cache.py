"""TrackerCache -- the derived record of what a tracker last said.

`details/external-work-management-architecture.md` calls this Stage 2 of the
sync ladder: "Never4gA maintains a derived local cache/index for faster
context/search. OpenProject remains authoritative."

It is derived, and it is disposable. `core/05` section 7 puts derived state
outside the vault, and `core/06` section 3 forbids a cached row from becoming
identity -- a :class:`~never4ga.domain.identity.WorkItemKey` is a *derived* key
over the provider's own, exactly as `core/03` section 17 permits.

**It is not the index, and it does not live in the index's database.**
``rebuild`` means "discard the projection and build it again from Markdown",
and a cache of somebody else's operational state cannot be rebuilt from
Markdown at all. Sharing one file would force ``rebuild`` to learn which tables
to spare, which is a worse thing to have to remember than a second file.

Both are still disposable. Deleting either costs a refetch and nothing more --
no durable fact lives here, because the tracker is the system of record for
every one of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from never4ga.domain.identity import WorkItemKey
from never4ga.ports.work_management import WorkItem

__all__ = ["CachedWorkItem", "TrackerCache"]


@dataclass(frozen=True, slots=True)
class CachedWorkItem:
    """One work item as the cache last saw it.

    `details/openproject-adapter.md` section 9 lists what an entry records:
    ``provider_updated_at``, ``fetched_at``, ``provider_version /
    lockVersion``, the normalized representation, and selected raw provider
    extension data.

    Only ``fetched_at`` is stored beside the item, because it is the only one
    that is not already *in* it: the normalized representation is ``item``, and
    section 9's other three are fields of it. Repeating them here would give a
    cached ticket two versions of its own update time, and nothing good happens
    when those disagree. A backend may still keep them as columns to query by;
    :attr:`provider_updated_at` is where it reads them from.
    """

    key: WorkItemKey
    item: WorkItem
    fetched_at: datetime

    def __post_init__(self) -> None:
        if self.fetched_at.tzinfo is None:
            # `fetched_at` is the one field here Never4gA writes itself, so it
            # is the one it can insist on. A naive instant compares against an
            # aware "now" by raising, deep inside `age`, at whatever moment the
            # first cached read happens to occur.
            raise ValueError("a cache entry's fetched_at must carry a timezone")

    @property
    def provider_updated_at(self) -> datetime | None:
        """When the tracker says the item last changed."""
        return self.item.updated_at

    @property
    def provider_version(self) -> str | None:
        """The optimistic-locking version, when the provider has one.

        Writes need the version from a fresh read, and the read path is what
        supplies it. Cached, it is a hint about what was current
        when it was fetched -- never a version to write against.
        """
        version = self.item.extra.get("lock_version")
        return None if version is None else str(version)

    def age(self, now: datetime) -> float:
        """Seconds since this entry was fetched."""
        return (now - self.fetched_at).total_seconds()


@runtime_checkable
class TrackerCache(Protocol):
    def get(self, key: WorkItemKey) -> CachedWorkItem | None: ...

    def put(self, entry: CachedWorkItem) -> None:
        """Record an item, replacing any earlier entry for the same key."""
        ...

    def put_many(self, entries: Sequence[CachedWorkItem]) -> None:
        """As :meth:`put`, for a whole page of search results at once."""
        ...

    def entries(self, connection: str, project_ref: str) -> Sequence[CachedWorkItem]:
        """Everything cached for one project, in a stable order.

        Ordered by external id, which is dull and deterministic. A caller that
        wants "recently updated first" sorts, because a cache should not decide
        what a view means.
        """
        ...

    def forget(self, key: WorkItemKey) -> None:
        """Drop one entry. Absent is not an error -- forgetting is idempotent."""
        ...

    def clear(self) -> None:
        """Drop everything. The next read costs a refetch and nothing else."""
        ...
