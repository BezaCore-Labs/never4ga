"""The derived database: pragmas, migrations, and disposability.

details/data-indexing-maintenance.md section 2 fixes the connection settings and
section 17 the invariant that matters most: deleting the file and rebuilding
recovers everything. Nothing here is canonical (core/06 section 2).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from never4ga.adapters.sqlite import SCHEMA_VERSION, applied_versions, open_index


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite3"


@pytest.fixture
def connection(database: Path) -> Iterator[sqlite3.Connection]:
    with closing(open_index(database)) as open_connection:
        yield open_connection


class TestConnectionSettings:
    def test_foreign_keys_are_enforced(self, connection: sqlite3.Connection) -> None:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_journal_mode_is_wal(self, connection: sqlite3.Connection) -> None:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"

    def test_a_busy_timeout_is_configured(self, connection: sqlite3.Connection) -> None:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] > 0

    def test_synchronous_is_normal(self, connection: sqlite3.Connection) -> None:
        # The documented WAL setting: FULL syncs the WAL per commit,
        # and a derived, rebuildable database has nothing that earns an fsync
        # per chunk. 1 is NORMAL.
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1

    def test_rows_are_addressable_by_column_name(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT 1 AS answer").fetchone()
        assert row["answer"] == 1


class TestMigrations:
    def test_opening_an_empty_file_creates_the_schema(self, connection: sqlite3.Connection) -> None:
        assert applied_versions(connection) == tuple(range(1, SCHEMA_VERSION + 1))

    def test_migrating_twice_changes_nothing(self, database: Path) -> None:
        with closing(open_index(database)) as connection:
            first = _schema_fingerprint(connection)
        with closing(open_index(database)) as connection:
            assert applied_versions(connection) == tuple(range(1, SCHEMA_VERSION + 1))
            assert _schema_fingerprint(connection) == first

    def test_the_expected_tables_exist(self, connection: sqlite3.Connection) -> None:
        expected = {
            "schema_migrations",
            "documents",
            "document_metadata",
            "tags",
            "document_tags",
            "domains",
            "document_domains",
            "links",
            "relations",
            "chunks",
            "indexed_paths",
        }
        assert expected <= _table_names(connection)

    def test_deleting_the_file_costs_nothing_but_a_rebuild(self, database: Path) -> None:
        # details/data-indexing-maintenance.md section 17. The projection is
        # disposable by design; losing it must never be a data loss event.
        with closing(open_index(database)) as connection:
            connection.execute(
                "INSERT INTO documents (id, path, type, title) VALUES (?, ?, ?, ?)",
                ("0198d71c-f0ad-71bd-949a-9276f9ce4e84", "a.md", "knowledge", "A"),
            )
            connection.commit()
        database.unlink()
        with closing(open_index(database)) as connection:
            assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
            assert applied_versions(connection) == tuple(range(1, SCHEMA_VERSION + 1))


class TestIdentityIsNeverABackendRow:
    def test_the_document_key_is_the_never4ga_uuid(self, connection: sqlite3.Connection) -> None:
        # core/06 section 3: a backend-native row ID is never canonical identity.
        columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(documents)")}
        assert columns["id"]["pk"] == 1
        assert columns["id"]["type"] == "TEXT"


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in rows}


def _schema_fingerprint(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    )
    return [(row["name"], row["sql"]) for row in rows]


class TestFtsRowidBackfill:
    """Migration 7: existing databases gain `chunks.fts_rowid`, filled
    from the FTS table in one staged pass rather than one scan per row, so the
    upgrade itself does not pay the quadratic it removes.
    """

    def test_an_old_database_is_backfilled(self, tmp_path: Path) -> None:
        from never4ga.adapters.sqlite.connection import MIGRATIONS, connect, migrate

        database = tmp_path / "old.sqlite3"
        with closing(connect(database)) as connection:
            migrate(connection, MIGRATIONS[:6])
            with connection:
                connection.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, path, ordinal, heading_path, text,
                        content_hash, policy_version, start_line, end_line, metadata_json
                    ) VALUES ('k1', 'd1', 'a.md', 0, 'H', 'body', 'h', 'v1', 1, 2, NULL)
                    """
                )
                connection.execute(
                    "INSERT INTO chunks_fts (chunk_id, title, heading_path, text, keywords)"
                    " VALUES ('k1', 't', 'H', 'body', '')"
                )
                fts_rowid = connection.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunk_id = 'k1'"
                ).fetchone()[0]

        with closing(open_index(database)) as connection:
            row = connection.execute(
                "SELECT fts_rowid FROM chunks WHERE chunk_id = 'k1'"
            ).fetchone()
            assert row["fts_rowid"] == fts_rowid

    def test_a_fresh_database_has_the_column(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(chunks)")}
        assert "fts_rowid" in columns
