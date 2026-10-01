"""TrackerCache over SQLite.

A cached item must be indistinguishable from a freshly fetched one. That is the
whole promise of a read-through cache, and it is the only hard problem here: a
normalised :class:`WorkItem` carries datetimes, dates and
:class:`ExternalId`\\ s inside ``extra``, and JSON has none of those. Storing it
naively hands back a string where the tracker handed back a ``date``, so a
caller would branch differently depending on whether the cache was warm -- the
worst kind of bug to find, because it only appears the second time.

So values are *tagged* on the way in and read back to their own types. The
alternative, a fixed column per field, was rejected for the reason
`core/02` gives about schema extensions: a provider's custom fields are not
knowable in advance and must survive a round trip unchanged.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Final

from never4ga.domain.identity import ExternalId, WorkItemKey
from never4ga.ports.tracker_cache import CachedWorkItem
from never4ga.ports.work_management import WorkItem

__all__ = ["SQLiteTrackerCache"]

_TAG: Final = "$type"

_COLUMNS: Final = (
    "connection, project_ref, external_id, provider, "
    "fetched_at, provider_updated_at, provider_version, normalized"
)


class SQLiteTrackerCache:
    """One vault's tracker cache. The caller owns the connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, key: WorkItemKey) -> CachedWorkItem | None:
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM work_items "
            "WHERE connection = ? AND project_ref = ? AND external_id = ?",
            (key.connection, key.project_ref, key.external_id.value),
        ).fetchone()
        return None if row is None else _to_entry(row)

    def put(self, entry: CachedWorkItem) -> None:
        self.put_many([entry])

    def put_many(self, entries: Sequence[CachedWorkItem]) -> None:
        if not entries:
            return
        with self._connection:
            self._connection.execute("BEGIN")
            self._connection.executemany(
                f"INSERT OR REPLACE INTO work_items ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [_to_row(entry) for entry in entries],
            )

    def entries(self, connection: str, project_ref: str) -> Sequence[CachedWorkItem]:
        rows = self._connection.execute(
            f"SELECT {_COLUMNS} FROM work_items "
            "WHERE connection = ? AND project_ref = ? ORDER BY external_id",
            (connection, project_ref),
        ).fetchall()
        return tuple(_to_entry(row) for row in rows)

    def forget(self, key: WorkItemKey) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM work_items "
                "WHERE connection = ? AND project_ref = ? AND external_id = ?",
                (key.connection, key.project_ref, key.external_id.value),
            )

    def clear(self) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM work_items")


def _to_row(entry: CachedWorkItem) -> tuple[Any, ...]:
    return (
        entry.key.connection,
        entry.key.project_ref,
        entry.key.external_id.value,
        entry.key.external_id.provider,
        entry.fetched_at.isoformat(),
        None if entry.provider_updated_at is None else entry.provider_updated_at.isoformat(),
        entry.provider_version,
        json.dumps(_encode_item(entry.item), separators=(",", ":")),
    )


def _to_entry(row: sqlite3.Row) -> CachedWorkItem:
    item = _decode_item(json.loads(row["normalized"]))
    return CachedWorkItem(
        key=WorkItemKey(
            connection=row["connection"],
            project_ref=row["project_ref"],
            external_id=ExternalId(provider=row["provider"], value=row["external_id"]),
        ),
        item=item,
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
    )


def _encode_item(item: WorkItem) -> dict[str, Any]:
    return {
        "ref": _encode(item.ref),
        "title": item.title,
        "status": item.status,
        "assignee": item.assignee,
        "priority": item.priority,
        "milestone": item.milestone,
        "updated_at": _encode(item.updated_at),
        "url": item.url,
        "relations": [_encode(reference) for reference in item.relations],
        "extra": {name: _encode(value) for name, value in item.extra.items()},
    }


def _decode_item(payload: Mapping[str, Any]) -> WorkItem:
    return WorkItem(
        ref=_decode(payload["ref"]),
        title=payload["title"],
        status=payload["status"],
        assignee=payload["assignee"],
        priority=payload["priority"],
        milestone=payload["milestone"],
        updated_at=_decode(payload["updated_at"]),
        url=payload["url"],
        relations=tuple(_decode(reference) for reference in payload["relations"]),
        extra={name: _decode(value) for name, value in payload["extra"].items()},
    )


def _encode(value: Any) -> Any:
    """Tag the types JSON does not have, and leave the ones it does.

    ``datetime`` is checked before ``date`` because it is a subclass of one, and
    an instant written as a day loses its time and its offset.
    """
    if isinstance(value, ExternalId):
        return {_TAG: "external_id", "provider": value.provider, "value": value.value}
    if isinstance(value, datetime):
        return {_TAG: "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {_TAG: "date", "value": value.isoformat()}
    if isinstance(value, Mapping):
        # A provider's extension data is arbitrary and nested, and a plain
        # mapping that happened to carry a `$type` key would be read back as
        # whatever it named. Tagging every mapping keeps the two apart.
        return {_TAG: "mapping", "value": {str(k): _encode(v) for k, v in value.items()}}
    if isinstance(value, list | tuple):
        # A tuple is not a list to a caller comparing two work items, and
        # "indistinguishable from a fresh fetch" has to survive whatever a
        # provider's extension data happens to be shaped like.
        return {
            _TAG: "sequence",
            "tuple": isinstance(value, tuple),
            "value": [_encode(each) for each in value],
        }
    return value


def _decode(value: Any) -> Any:
    if not isinstance(value, Mapping) or _TAG not in value:
        return value
    tag = value[_TAG]
    if tag == "external_id":
        return ExternalId(provider=value["provider"], value=value["value"])
    if tag == "datetime":
        return datetime.fromisoformat(value["value"])
    if tag == "date":
        return date.fromisoformat(value["value"])
    if tag == "mapping":
        return {name: _decode(each) for name, each in value["value"].items()}
    if tag == "sequence":
        decoded = [_decode(each) for each in value["value"]]
        return tuple(decoded) if value.get("tuple") else decoded
    return value
