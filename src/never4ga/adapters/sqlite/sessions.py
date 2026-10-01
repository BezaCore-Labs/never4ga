"""The session database: its own file, beside the index and the tracker cache.

It is a separate file for the same reason the tracker cache is: a store with
a different lifecycle should not force ``rebuild`` to remember which tables to
spare.

**The lifecycle here is different in the other direction.** The index and the
tracker cache are derived and disposable -- deleting them costs an ``index`` or
a refetch. This one is neither. Nothing in the vault ever held a checkpoint, so
nothing can rebuild one, and deleting this file loses what it held. Back it up
with the other local state that is not derived (`details/security-configuration.md`
section 10).

The connection settings are the index's -- WAL and a busy timeout -- because
the shape is the same: one writer at a time, concurrent readers, and two
clients that may checkpoint in the same minute.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

from never4ga.adapters.sqlite.connection import BUSY_TIMEOUT_MS, connect, migrate
from never4ga.errors import SessionStoreError

__all__ = ["SCHEMA_VERSION", "SESSION_MIGRATIONS", "open_sessions"]

#: Migration 1 -- one row per session, one per checkpoint.
_INITIAL_SCHEMA: Final = (
    """
    CREATE TABLE sessions (
        id           TEXT PRIMARY KEY,
        workspace    TEXT NOT NULL,
        actor        TEXT NOT NULL,
        client       TEXT,
        started_at   TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    )
    """,
    # "What happened in this workspace recently" is the cross-client question
    # asked on every startup. It must not become a table scan on a
    # machine that has been running for a year.
    """
    CREATE INDEX sessions_recent ON sessions (workspace, last_seen_at DESC)
    """,
    # `sequence` rather than a bare timestamp order: two checkpoints recorded in
    # the same instant must come back in the order they were appended, and an
    # autoincrementing key is the only thing that remembers that.
    """
    CREATE TABLE checkpoints (
        sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
        session     TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
        recorded_at TEXT NOT NULL,
        note        TEXT NOT NULL,
        actions     TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX checkpoints_of_session ON checkpoints (session, recorded_at, sequence)
    """,
)

#: Migration 2 -- what `wrap` wrote, so wrapping twice updates one document
#: rather than writing a second. Nullable: most sessions have not been wrapped
#: yet, and one that never is stays a perfectly good session.
_WRAP_SCHEMA: Final = ("ALTER TABLE sessions ADD COLUMN wrapped_log TEXT",)

#: Migration 3 -- checkpoint declarations. Two columns rather than one, because a
#: decision and a thing worth remembering are answered differently by whoever
#: reads them back: one becomes an ADR, the other a note. `DEFAULT '[]'` fills
#: the rows already there, so a checkpoint written before this migration reads
#: back as having declared nothing, which is exactly what happened.
_DECLARATION_SCHEMA: Final = (
    "ALTER TABLE checkpoints ADD COLUMN decisions TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE checkpoints ADD COLUMN memories TEXT NOT NULL DEFAULT '[]'",
)

#: Migration 4 -- columns for a tracker comment `wrap` no longer leaves.
#: Migration 5 drops them. The entry stays because a migration list is
#: append-only, and a database that ran this one needs the step that undoes it
#: rather than a gap.
_TRACKER_SCHEMA: Final = (
    "ALTER TABLE sessions ADD COLUMN wrapped_ticket TEXT",
    "ALTER TABLE sessions ADD COLUMN wrapped_comment TEXT",
)

#: Migration 5 -- drop the tracker-comment columns. A comment saying a session
#: happened, with a vault path, gives a ticket's reader nothing usable.
#: `core/04` section 37 asks wrap to say "what work items *may need* an
#: update", which is a candidate rather than a narration.
#:
#: Checkpoints gain a third declaration, `work`, beside `decisions` and
#: `memories`. `DEFAULT '[]'` fills the rows already there, which read back as
#: having declared nothing -- exactly what happened.
_WORK_DECLARATION_SCHEMA: Final = (
    "ALTER TABLE checkpoints ADD COLUMN work TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE sessions DROP COLUMN wrapped_ticket",
    "ALTER TABLE sessions DROP COLUMN wrapped_comment",
)

#: Migration 6 -- the tracker writes Never4gA performed for a session. `wrap`
#: writes nothing to a tracker itself; the agent does, and this table is how
#: `wrap` can tell that it did.
#:
#: Deliberately not a column on `checkpoints`. A declaration is a claim recorded
#: verbatim and an action is a write Never4gA performed, and storing them as one
#: thing would make an unverified claim indistinguishable from an observation.
_WORK_ACTION_SCHEMA: Final = (
    """
    CREATE TABLE work_actions (
        session     TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
        recorded_at TEXT NOT NULL,
        item        TEXT NOT NULL,
        verb        TEXT NOT NULL,
        detail      TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX work_actions_of_session ON work_actions (session, item)",
)


#: Migration 7 -- context changes (`core/04` section 37). A checkpoint can
#: declare a context document the session changed, and a startup records every
#: context document its pack carried, with a digest of the body as it stood.
#: `wrap` compares the two.
#:
#: Its own table rather than a column on `sessions`, because a session is given
#: several documents and each is compared on its own. `UNIQUE` is what makes the
#: first record win: what a session was given is fixed when it opened.
_CONTEXT_SCHEMA: Final = (
    "ALTER TABLE checkpoints ADD COLUMN context TEXT NOT NULL DEFAULT '[]'",
    """
    CREATE TABLE given_context (
        sequence   INTEGER PRIMARY KEY AUTOINCREMENT,
        session    TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
        concept_id TEXT NOT NULL,
        path       TEXT NOT NULL,
        title      TEXT NOT NULL,
        digest     TEXT NOT NULL,
        UNIQUE (session, concept_id)
    )
    """,
)


SESSION_MIGRATIONS: Final = (
    _INITIAL_SCHEMA,
    _WRAP_SCHEMA,
    _DECLARATION_SCHEMA,
    _TRACKER_SCHEMA,
    _WORK_DECLARATION_SCHEMA,
    _WORK_ACTION_SCHEMA,
    _CONTEXT_SCHEMA,
)

SCHEMA_VERSION: Final = len(SESSION_MIGRATIONS)


def open_sessions(
    database: Path | str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS
) -> sqlite3.Connection:
    """Connect and migrate. The caller owns the connection and closes it.

    Failures here arrive as :class:`SessionStoreError`. Connecting and
    migrating is where a full disk, a read-only path, a lock held past the
    busy timeout and a corrupt file actually land -- before any statement this
    store's methods run -- and a caller of the port may not catch ``sqlite3``
    to find out. Translating at the point the driver is used keeps the
    knowledge of it inside the adapter.
    """
    try:
        connection = connect(database, busy_timeout_ms=busy_timeout_ms)
    except sqlite3.Error as error:
        raise SessionStoreError(
            f"the session store at {database} could not be opened: {error}"
        ) from error
    try:
        migrate(connection, SESSION_MIGRATIONS)
        # The cascade on `checkpoints.session` is only enforced when foreign
        # keys are on, and SQLite defaults them off per connection.
        connection.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as error:
        connection.close()
        raise SessionStoreError(
            f"the session store at {database} could not be migrated: {error}"
        ) from error
    except BaseException:
        connection.close()
        raise
    return connection
