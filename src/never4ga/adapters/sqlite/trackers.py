"""The tracker cache database: its own file, beside the index.

It is a separate file because of what ``rebuild`` means: it discards the
projection and builds it again from Markdown, and a cache of somebody else's
operational state cannot be rebuilt from Markdown at all. One shared file
would force ``rebuild`` to learn which tables to preserve -- a worse thing to
have to remember than a second file.

Both databases are derived and both are disposable. Deleting this one costs a
refetch; deleting the index costs an ``index``. Neither holds a durable fact.

The connection settings are the index's (`connect`, WAL and a busy timeout),
because the reasons for them -- one writer, concurrent readers -- are the same.
Only the schema differs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

from never4ga.adapters.sqlite.connection import BUSY_TIMEOUT_MS, connect, migrate

__all__ = ["SCHEMA_VERSION", "TRACKER_MIGRATIONS", "open_trackers"]

#: Migration 1 -- one row per cached work item.
#:
#: The primary key is core/03 section 17's three terms, in the order that makes
#: a project's entries a contiguous range. `details/openproject-adapter.md`
#: section 9's fields are columns *and* inside ``normalized``: the columns exist
#: to be queried and compared without decoding every row, and the JSON is the
#: entry. Where they could disagree, the JSON wins -- it is what a reader gets
#: back -- so the columns are written from it rather than passed in beside it.
_INITIAL_SCHEMA: Final = (
    """
    CREATE TABLE work_items (
        connection          TEXT NOT NULL,
        project_ref         TEXT NOT NULL,
        external_id         TEXT NOT NULL,
        provider            TEXT NOT NULL,
        fetched_at          TEXT NOT NULL,
        provider_updated_at TEXT,
        provider_version    TEXT,
        normalized          TEXT NOT NULL,
        PRIMARY KEY (connection, project_ref, external_id)
    )
    """,
    # "What changed since I last looked" is the question a refresh asks, and it
    # must not become a table scan on a tracker with thousands of items.
    """
    CREATE INDEX work_items_updated
        ON work_items (connection, project_ref, provider_updated_at)
    """,
)

TRACKER_MIGRATIONS: Final = (_INITIAL_SCHEMA,)

SCHEMA_VERSION: Final = len(TRACKER_MIGRATIONS)


def open_trackers(
    database: Path | str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS
) -> sqlite3.Connection:
    """Connect and migrate. The caller owns the connection and closes it."""
    connection = connect(database, busy_timeout_ms=busy_timeout_ms)
    try:
        migrate(connection, TRACKER_MIGRATIONS)
    except BaseException:
        connection.close()
        raise
    return connection
