"""In-memory TrackerCache."""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.identity import WorkItemKey
from never4ga.ports.tracker_cache import CachedWorkItem

__all__ = ["InMemoryTrackerCache"]


class InMemoryTrackerCache:
    """A tracker cache backed by a dictionary.

    The reference implementation the SQLite one is diffed against (core/06
    section 25): entries are frozen dataclasses, so storing one is storing it,
    and any difference between the two backends is the encoding.
    """

    def __init__(self) -> None:
        self._entries: dict[WorkItemKey, CachedWorkItem] = {}

    def get(self, key: WorkItemKey) -> CachedWorkItem | None:
        return self._entries.get(key)

    def put(self, entry: CachedWorkItem) -> None:
        self._entries[entry.key] = entry

    def put_many(self, entries: Sequence[CachedWorkItem]) -> None:
        for entry in entries:
            self.put(entry)

    def entries(self, connection: str, project_ref: str) -> Sequence[CachedWorkItem]:
        found = [
            entry
            for key, entry in self._entries.items()
            if key.connection == connection and key.project_ref == project_ref
        ]
        found.sort(key=lambda entry: entry.key.external_id.value)
        return tuple(found)

    def forget(self, key: WorkItemKey) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
