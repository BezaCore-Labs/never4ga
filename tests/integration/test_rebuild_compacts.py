"""`rebuild` gives the disk back.

`auto_vacuum` is NONE, so every reindex that replaces a document leaves its old
pages on the freelist. Without `VACUUM` the file only ever grows, and a
long-lived index can be several times the size of its live data.

**Compaction belongs to `rebuild` and to nothing else.** `rebuild` already
means "discard every projection and build it again from Markdown", so
returning the file to the size of its contents finishes that job, and the cost
is paid by somebody who asked for it. `VACUUM` is deliberately not on the
maintenance scheduler: it takes an exclusive lock for the length of a
whole-file rewrite, which would stall everything else in the background.

`reconcile` never compacts. It is the common path, it runs on every watcher
batch, and rewriting the database each time would be the same defect in
another form.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from never4ga.adapters.sqlite import open_index
from never4ga.adapters.sqlite.connection import compact


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite3"


@pytest.fixture
def connection(database: Path) -> Iterator[sqlite3.Connection]:
    with closing(open_index(database)) as open_connection:
        yield open_connection


def freelist(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA freelist_count").fetchone()[0])


def pages(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA page_count").fetchone()[0])


def bloat(connection: sqlite3.Connection) -> None:
    """Fill the database and empty it again, which is what leaves a freelist.

    Only its own rows are removed: a helper that emptied the table would make
    `test_what_survives_is_everything` pass by deleting the row it is meant to
    prove survives.
    """
    with connection:
        for index in range(2_000):
            connection.execute(
                """
                INSERT INTO chunks (
                    chunk_id, document_id, path, ordinal, heading_path, text,
                    content_hash, policy_version, start_line, end_line, metadata_json
                ) VALUES (?, 'd', 'a.md', 0, 'H', ?, 'h', 'v1', 1, 2, NULL)
                """,
                (f"pad{index}", "padding " * 200),
            )
    with connection:
        connection.execute("DELETE FROM chunks WHERE chunk_id LIKE 'pad%'")


class TestCompaction:
    def test_deleting_rows_leaves_the_pages_behind(self, connection: sqlite3.Connection) -> None:
        # The premise, stated rather than assumed: deleted rows leave their
        # pages behind, so the file outgrows its contents.
        bloat(connection)
        assert freelist(connection) > 100

    def test_compacting_returns_them(self, connection: sqlite3.Connection) -> None:
        bloat(connection)
        compact(connection)
        assert freelist(connection) == 0

    def test_the_database_actually_shrinks(self, connection: sqlite3.Connection) -> None:
        # Counted in pages rather than bytes on disk: in WAL mode the main file
        # keeps its size until a checkpoint, so a byte comparison would be
        # measuring when SQLite got round to it rather than what VACUUM did.
        bloat(connection)
        before = pages(connection)
        compact(connection)
        assert pages(connection) < before

    def test_compacting_a_tidy_database_is_harmless(
        self, connection: sqlite3.Connection, database: Path
    ) -> None:
        compact(connection)
        assert freelist(connection) == 0
        assert database.exists()

    def test_what_survives_is_everything(self, connection: sqlite3.Connection) -> None:
        # A compaction that lost a row would be indistinguishable from a
        # successful one until somebody searched for the row.
        with connection:
            connection.execute(
                """
                INSERT INTO chunks (
                    chunk_id, document_id, path, ordinal, heading_path, text,
                    content_hash, policy_version, start_line, end_line, metadata_json
                ) VALUES ('keep', 'd', 'a.md', 0, 'H', 'body', 'h', 'v1', 1, 2, NULL)
                """
            )
        bloat(connection)
        compact(connection)
        rows = connection.execute("SELECT chunk_id FROM chunks").fetchall()
        assert [row["chunk_id"] for row in rows] == ["keep"]


class TestTheSessionRebuildCompacts:
    """`rebuild` and compaction travel together, because roots forget.

    The same reasoning `VaultSession.diagnose` carries: a root that rebuilt
    without compacting would leave the file at whatever size its worst day
    made it, and nothing would ever say so.
    """

    def _session(self, compact_hook: object) -> object:
        from never4ga.adapters.fakes import (
            InMemoryDocumentStore,
            InMemoryGraphIndex,
            InMemoryIndexState,
            InMemoryMetadataIndex,
            InMemoryTextIndex,
            InMemoryVaultFileStore,
        )
        from never4ga.domain.identity import ConceptId
        from never4ga.services.session import VaultSession

        return VaultSession(
            vault_id=ConceptId.new(),
            files=InMemoryVaultFileStore(),
            documents=InMemoryDocumentStore(),
            metadata=InMemoryMetadataIndex(),
            text=InMemoryTextIndex(),
            graph=InMemoryGraphIndex(),
            state=InMemoryIndexState(),
            compact=compact_hook,  # type: ignore[arg-type]
        )

    def test_rebuilding_compacts(self) -> None:
        called = {"n": 0}
        session = self._session(lambda: called.__setitem__("n", called["n"] + 1))
        session.rebuild()  # type: ignore[attr-defined]
        assert called["n"] == 1

    def test_reconciling_does_not(self) -> None:
        # The common path, run on every watcher batch. Rewriting the whole
        # database each time would be worse than a file that is too large.
        called = {"n": 0}
        session = self._session(lambda: called.__setitem__("n", called["n"] + 1))
        session.indexer.reconcile()  # type: ignore[attr-defined]
        assert called["n"] == 0

    def test_a_store_with_nothing_to_reclaim_still_rebuilds(self) -> None:
        session = self._session(None)
        assert session.rebuild() is not None  # type: ignore[attr-defined]
