"""The derived SQLite database: connection settings and migrations.

SQLite is the first *projection* backend, not the domain model (core/06 sections
2 and 5). Everything defined here is derived from canonical Markdown and must
survive being deleted: `details/data-indexing-maintenance.md` section 17 requires
that removing ``index.sqlite3`` and rebuilding recovers the lot.

Two consequences shape the schema.

**Never4gA UUIDs are the keys.** core/06 section 3 forbids a backend-native row
ID from being canonical identity, so ``documents.id`` is the UUIDv7 text and no
table hangs its meaning on a rowid.

**The projections are separately usable.** ``chunks``, ``links`` and
``relations`` carry no foreign key to ``documents``. A TextIndex or GraphIndex is
a backend in its own right (core/06 section 5) and its contract suite exercises
it with no metadata projection present at all; requiring a ``documents`` row
first would weld three independent ports into one. Referential integrity that
*is* per-document -- tags, domains, extension metadata -- is enforced, and
cascades on delete.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Final

__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "applied_versions",
    "compact",
    "connect",
    "migrate",
    "open_index",
]

#: `details/data-indexing-maintenance.md` section 2: "A suitable busy timeout
#: should be configured." WAL improves local concurrency, but the runtime still
#: prefers one writer, so this covers a reader waiting on a checkpoint rather
#: than contention between peers.
BUSY_TIMEOUT_MS: Final = 5_000

_MIGRATION_TABLE: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

#: Migration 1 -- the document projection, its vocabularies, its edges and its
#: chunks (`details/data-indexing-maintenance.md` sections 3 to 5).
_INITIAL_SCHEMA: Final = (
    """
    CREATE TABLE documents (
        id               TEXT PRIMARY KEY,
        path             TEXT NOT NULL,
        type             TEXT NOT NULL,
        schema_version   TEXT,
        title            TEXT NOT NULL,
        description      TEXT,
        status           TEXT,
        authority        TEXT,
        lifecycle        TEXT,
        workspace_id     TEXT,
        created_at       TEXT,
        generated_at     TEXT,
        stale_after      TEXT,
        content_hash     TEXT,
        file_size        INTEGER,
        filesystem_mtime REAL,
        indexed_at       TEXT,
        validation_state TEXT
    )
    """,
    # Not UNIQUE. Two documents at one path is a maintenance finding, not a
    # crash: core/02 section 32 reports contradictions and section 23 keeps
    # repair explicit. A projection that refuses to load a broken vault cannot
    # tell the user what is broken about it.
    "CREATE INDEX documents_path ON documents (path)",
    "CREATE INDEX documents_type ON documents (type)",
    "CREATE INDEX documents_workspace ON documents (workspace_id)",
    """
    CREATE TABLE document_metadata (
        document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        key         TEXT NOT NULL,
        value_json  TEXT NOT NULL,
        PRIMARY KEY (document_id, key)
    )
    """,
    "CREATE TABLE tags (tag TEXT PRIMARY KEY)",
    """
    CREATE TABLE document_tags (
        document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        tag         TEXT NOT NULL REFERENCES tags (tag),
        PRIMARY KEY (document_id, tag)
    )
    """,
    "CREATE INDEX document_tags_tag ON document_tags (tag)",
    "CREATE TABLE domains (domain TEXT PRIMARY KEY)",
    """
    CREATE TABLE document_domains (
        document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        domain      TEXT NOT NULL REFERENCES domains (domain),
        PRIMARY KEY (document_id, domain)
    )
    """,
    "CREATE INDEX document_domains_domain ON document_domains (domain)",
    """
    CREATE TABLE links (
        source_document_id TEXT NOT NULL,
        target_path        TEXT,
        target_document_id TEXT,
        anchor             TEXT,
        resolved           INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX links_source ON links (source_document_id)",
    "CREATE INDEX links_target ON links (target_document_id)",
    """
    CREATE TABLE relations (
        source_id       TEXT NOT NULL,
        relation_type   TEXT NOT NULL,
        target_id       TEXT NOT NULL,
        attributes_json TEXT,
        PRIMARY KEY (source_id, relation_type, target_id)
    )
    """,
    "CREATE INDEX relations_target ON relations (target_id, relation_type)",
    """
    CREATE TABLE chunks (
        chunk_id       TEXT PRIMARY KEY,
        document_id    TEXT NOT NULL,
        path           TEXT NOT NULL,
        ordinal        INTEGER NOT NULL,
        heading_path   TEXT NOT NULL,
        text           TEXT NOT NULL,
        content_hash   TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        start_line     INTEGER NOT NULL,
        end_line       INTEGER NOT NULL,
        metadata_json  TEXT
    )
    """,
    "CREATE INDEX chunks_document ON chunks (document_id, ordinal)",
)

#: Migration 2 -- the lexical index (`details/data-indexing-maintenance.md`
#: section 7, core/05 section 21).
#:
#: Standalone rather than external-content over ``chunks``. It costs a second
#: copy of the chunk text and buys two things worth more than the disk: the FTS
#: table can carry columns ``chunks`` has no business holding -- a document's
#: title and keywords, repeated per chunk so BM25 can weight them -- and
#: ``chunks`` stays a plain table that a future PostgreSQL FTS or OpenSearch
#: adapter can read without inheriting FTS5's storage (core/06 section 18).
_FULL_TEXT_SCHEMA: Final = (
    """
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        chunk_id UNINDEXED,
        title,
        heading_path,
        text,
        keywords,
        tokenize = 'unicode61 remove_diacritics 2'
    )
    """,
)

#: Migration 3 -- a link that names its target instead of locating it.
#:
#: `details/data-indexing-maintenance.md` section 4 gives ``links`` a
#: ``target_path``, which assumes every link is path-based. A bare ``[[Note]]``
#: is not: Obsidian resolves it against the whole vault by name. The column is
#: additive and mutually exclusive with ``target_path`` -- one or the other, per
#: link -- which also means the distinction needs no separate style column.
_LINK_NAMES: Final = ("ALTER TABLE links ADD COLUMN target_name TEXT",)

#: Migration 4 -- vectors, beside the chunks they were made from: a plain table
#: of BLOBs, keyed by chunk, `namespace` and `vector_space` (core/06 sections 10
#: and 11). Migration 6 drops it again; the entry stays because a released
#: migration is never edited.
_VECTORS: Final = (
    """
    CREATE TABLE vectors (
        chunk_id      TEXT NOT NULL,
        document_id   TEXT NOT NULL,
        namespace     TEXT NOT NULL,
        vector_space  TEXT NOT NULL,
        dimension     INTEGER NOT NULL,
        vector        BLOB NOT NULL,
        magnitude     REAL NOT NULL,
        metadata_json TEXT,
        PRIMARY KEY (chunk_id, namespace, vector_space)
    )
    """,
    "CREATE INDEX vectors_scope ON vectors (namespace, vector_space, document_id)",
)

#: Migration 5 -- `details/data-indexing-maintenance.md` section 20's findings
#: table. In the index's own database rather than a file of its own, because a
#: finding is re-derivable from Markdown by running detection again -- so
#: `rebuild` discarding it is correct, which is exactly what is *not* true of
#: the tracker cache (`ports/tracker_cache.py`).
#:
#: `fingerprint` is what matches a finding across runs and `finding_id` is an
#: opaque row id, kept apart because `core/06` section 3 forbids a row id from
#: carrying meaning. The partial unique index is the whole reconciliation rule
#: in one line: at most one *open* finding per fingerprint, while any number of
#: resolved ones may accumulate as the history of a problem that keeps coming
#: back.
_MAINTENANCE_FINDINGS: Final = (
    """
    CREATE TABLE maintenance_findings (
        finding_id    TEXT PRIMARY KEY,
        rule          TEXT NOT NULL,
        severity      TEXT NOT NULL,
        fingerprint   TEXT NOT NULL,
        message       TEXT NOT NULL,
        document_id   TEXT,
        path          TEXT,
        detected_at   TEXT NOT NULL,
        resolved_at   TEXT,
        details_json  TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX maintenance_findings_open
    ON maintenance_findings (fingerprint)
    WHERE resolved_at IS NULL
    """,
    "CREATE INDEX maintenance_findings_rule ON maintenance_findings (rule, resolved_at)",
)

#: Migration 6 -- drop the vectors.
#:
#: Migration 4 stays exactly as written, because a released migration is never
#: edited: an existing database has already run it, and rewriting history would
#: leave those two databases disagreeing about what version 4 meant. So the
#: table is created and then dropped, which is the honest record of what
#: happened.
#:
#: Nothing canonical is lost. A vector was always derived from Markdown that is
#: still there, and `core/00` #1 is why this is a `DROP TABLE` rather than a
#: migration somebody has to think about: the only thing in here that could not
#: be recomputed is nothing.
_DROP_VECTORS: Final = (
    "DROP INDEX IF EXISTS vectors_scope",
    "DROP TABLE IF EXISTS vectors",
)

#: Migration 7 -- the FTS row becomes addressable.
#:
#: ``chunks_fts.chunk_id`` is UNINDEXED -- correctly, since indexing it would
#: tokenize identifiers into the search index -- but an UNINDEXED column gives
#: the planner nothing, so a delete addressed by it is a full scan of the
#: virtual table. One scan per chunk written makes a full reconcile
#: O(chunks²), and on a vault with thousands of chunks that holds the write
#: lock for minutes.
#:
#: So ``chunks`` records the FTS rowid at insert, and deletes address the
#: virtual table the one way FTS5 can seek: by rowid. This is plumbing between
#: two derived tables, not identity -- ``chunk_id`` remains the key everything
#: means (core/06 §3), and the whole database is still disposable.
#:
#: The backfill is staged through a real table so the migration itself does
#: not pay the quadratic it removes: one scan of ``chunks_fts``, one index,
#: one joined update, instead of one scan per chunk.
_FTS_ROWID: Final = (
    "ALTER TABLE chunks ADD COLUMN fts_rowid INTEGER",
    "CREATE TABLE fts_rowid_backfill AS SELECT rowid AS fts_rowid, chunk_id FROM chunks_fts",
    "CREATE INDEX fts_rowid_backfill_chunk ON fts_rowid_backfill (chunk_id)",
    """
    UPDATE chunks SET fts_rowid = (
        SELECT b.fts_rowid FROM fts_rowid_backfill b WHERE b.chunk_id = chunks.chunk_id
    )
    """,
    "DROP TABLE fts_rowid_backfill",
)

#: Migration 8 -- a chunk may be owned by a path (core/02 section 3.3).
#:
#: A foreign note has no concept, so ``document_id`` becomes nullable and
#: ``path`` is the owner of a row where it is NULL. SQLite cannot drop a NOT
#: NULL constraint in place, so the table is rebuilt: the same columns, the
#: same index, migration 7's ``fts_rowid`` carried across. Every existing row
#: keeps its ``document_id``, and the FTS table is untouched -- ``chunks``
#: remembers each row's FTS rowid, and a rebuild of the plain table does not
#: move those.
_PATH_OWNED_CHUNKS: Final = (
    """
    CREATE TABLE chunks_v8 (
        chunk_id       TEXT PRIMARY KEY,
        document_id    TEXT,
        path           TEXT NOT NULL,
        ordinal        INTEGER NOT NULL,
        heading_path   TEXT NOT NULL,
        text           TEXT NOT NULL,
        content_hash   TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        start_line     INTEGER NOT NULL,
        end_line       INTEGER NOT NULL,
        metadata_json  TEXT,
        fts_rowid      INTEGER
    )
    """,
    """
    INSERT INTO chunks_v8 (
        chunk_id, document_id, path, ordinal, heading_path, text, content_hash,
        policy_version, start_line, end_line, metadata_json, fts_rowid
    )
    SELECT chunk_id, document_id, path, ordinal, heading_path, text, content_hash,
           policy_version, start_line, end_line, metadata_json, fts_rowid
    FROM chunks
    """,
    "DROP TABLE chunks",
    "ALTER TABLE chunks_v8 RENAME TO chunks",
    "CREATE INDEX chunks_document ON chunks (document_id, ordinal)",
    "CREATE INDEX chunks_path_owned ON chunks (path) WHERE document_id IS NULL",
)

#: Migration 9 -- the index remembers a foreign note by its path.
#:
#: What ``documents`` keeps for a concept -- the hash it was indexed from and
#: the cheap stat -- kept for a note that has no concept, in a table of its
#: own. Not a row in ``documents``: that table is the metadata projection's
#: too, and a foreign note belongs to no metadata projection.
#: The key is a path because a path is all such a note has, and it is derived
#: like everything here: a rename is a new row and the old one deleted.
_INDEXED_PATHS: Final = (
    """
    CREATE TABLE indexed_paths (
        path             TEXT PRIMARY KEY,
        content_hash     TEXT NOT NULL,
        indexed_at       TEXT NOT NULL,
        file_size        INTEGER,
        filesystem_mtime REAL
    )
    """,
)

#: Ordered and append-only. A released migration is never edited: the way to
#: change the schema is to add the next one, so an existing database can always
#: be brought forward -- and, failing that, deleted and rebuilt.
MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    _INITIAL_SCHEMA,
    _FULL_TEXT_SCHEMA,
    _LINK_NAMES,
    _VECTORS,
    _MAINTENANCE_FINDINGS,
    _DROP_VECTORS,
    _FTS_ROWID,
    _PATH_OWNED_CHUNKS,
    _INDEXED_PATHS,
)

SCHEMA_VERSION: Final = len(MIGRATIONS)


def connect(database: Path | str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Open the derived database with the settings section 2 requires."""
    connection = sqlite3.connect(database, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    # NORMAL is the documented setting for WAL -- the default FULL syncs the
    # WAL on every commit, and the adapters commit per statement group, so a
    # full reconcile would pay tens of thousands of fsyncs. What NORMAL
    # risks is durability of the last commits across a power loss, and this
    # entire database is derived and rebuildable (section 17): the recovery
    # from any corruption is delete and reindex, which is also the recovery
    # from nothing having been synced at all.
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return connection


def compact(connection: sqlite3.Connection) -> None:
    """Return the file to the size of what is in it.

    ``auto_vacuum`` is NONE, so every reindex that replaces a document leaves
    its old pages on the freelist, and without ``VACUUM`` the file only ever
    grows.

    **Only ``rebuild`` calls this.** ``rebuild`` already means "discard every
    projection and build it again from Markdown", so returning the disk is
    finishing that job rather than a new policy, and the cost lands on somebody
    who asked for it. ``VACUUM`` on the maintenance scheduler was the other
    option and is deliberately not taken: it holds an exclusive lock for a
    whole-file rewrite, and a background exclusive lock stalls every client
    waiting to write.

    It must run outside a transaction, which it does: :func:`connect` opens in
    autocommit (``isolation_level=None``).
    """
    connection.execute("VACUUM")


def migrate(
    connection: sqlite3.Connection,
    migrations: Sequence[Sequence[str]] = MIGRATIONS,
) -> tuple[int, ...]:
    """Bring the database up to date. Returns what it applied.

    ``migrations`` defaults to the index's. The tracker cache and the session
    store are databases of their own, and one migration mechanism serves them
    all rather than each keeping a copy of this loop.
    Each database keeps its own ``schema_migrations`` table, because each is a
    separate file with a separate history.
    """
    connection.execute(_MIGRATION_TABLE)
    already = set(applied_versions(connection))
    newly: list[int] = []
    for version, statements in enumerate(migrations, start=1):
        if version in already:
            continue
        with connection:
            connection.execute("BEGIN")
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        newly.append(version)
    return tuple(newly)


def applied_versions(connection: sqlite3.Connection) -> tuple[int, ...]:
    rows: Sequence[sqlite3.Row] = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    return tuple(int(row["version"]) for row in rows)


def open_index(
    database: Path | str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS
) -> sqlite3.Connection:
    """Connect and migrate. The caller owns the connection and closes it."""
    connection = connect(database, busy_timeout_ms=busy_timeout_ms)
    try:
        migrate(connection)
    except BaseException:
        connection.close()
        raise
    return connection
