"""Every SessionStore implementation, against the one contract."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from never4ga.adapters.fakes.session_store import InMemorySessionStore
from never4ga.adapters.sqlite.session_store import SQLiteSessionStore
from never4ga.adapters.sqlite.sessions import open_sessions
from never4ga.composition import FileSessionStore
from never4ga.domain.sessions import SessionStateError, WorkAction
from never4ga.errors import SessionStoreError
from never4ga.ports.session_store import SessionStore
from tests.contracts.session_store_contract import (
    SessionStoreContract,
    make_checkpoint,
    make_session,
)

pytestmark = pytest.mark.contract


class TestInMemorySessionStore(SessionStoreContract):
    @pytest.fixture
    def store(self) -> SessionStore:
        return InMemorySessionStore()


class TestSQLiteSessionStore(SessionStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[SessionStore]:
        connection = open_sessions(tmp_path / "sessions.sqlite3")
        try:
            yield SQLiteSessionStore(connection)
        finally:
            connection.close()


class TestTheSessionDatabaseIsDurableNotDerived:
    """The property that separates this store from every other SQLite file.

    The index and the tracker cache are derived and disposable; this one is
    not, because nothing in the vault ever holds a checkpoint.
    """

    def test_two_writers_append_without_losing_either(self, tmp_path: Path) -> None:
        # Two clients checkpointing in the same minute is the ordinary case,
        # not the exotic one, and it is why this store is SQLite rather than JSON.
        database = tmp_path / "sessions.sqlite3"
        opener = open_sessions(database)
        session = make_session()
        SQLiteSessionStore(opener).open(session)

        first = open_sessions(database)
        second = open_sessions(database)
        try:
            claude, codex = SQLiteSessionStore(first), SQLiteSessionStore(second)
            for index in range(10):
                claude.append(make_checkpoint(session, note=f"claude {index}"))
                codex.append(make_checkpoint(session, note=f"codex {index}"))
            notes = [c.note for c in SQLiteSessionStore(opener).checkpoints(session.id)]
        finally:
            first.close()
            second.close()
            opener.close()

        assert len(notes) == 20
        assert set(notes) == {f"{who} {i}" for who in ("claude", "codex") for i in range(10)}

    def test_it_survives_being_reopened(self, tmp_path: Path) -> None:
        database = tmp_path / "sessions.sqlite3"
        session = make_session()
        first = open_sessions(database)
        try:
            store = SQLiteSessionStore(first)
            store.open(session)
            store.append(make_checkpoint(session, note="before the restart"))
        finally:
            first.close()

        second = open_sessions(database)
        try:
            reopened = SQLiteSessionStore(second)
            assert reopened.get(session.id) is not None
            assert [c.note for c in reopened.checkpoints(session.id)] == ["before the restart"]
        finally:
            second.close()


class TestTheStoresOwnFailuresAreTranslated:
    """A store failure surfaces as :class:`SessionStoreError`, never ``sqlite3``.

    `services` may not import `adapters`, so a caller cannot catch ``sqlite3``.
    A locked database, a full disk, a failed migration or a corrupt file must
    arrive as a declared error, distinct from :class:`SessionStateError` for a
    caller asking for something incoherent. That includes a caller that runs
    *after* a tracker write has already landed.
    """

    def test_a_corrupt_database_cannot_be_opened(self, tmp_path: Path) -> None:
        database = tmp_path / "sessions.sqlite3"
        # A real file, the right name, and not a database. Exactly what a
        # truncated write or a half-synced copy leaves behind.
        database.write_bytes(b"this is not a database" * 100)

        with pytest.raises(SessionStoreError):
            open_sessions(database)

    def test_a_statement_against_a_broken_database_is_translated(self, tmp_path: Path) -> None:
        database = tmp_path / "sessions.sqlite3"
        connection = open_sessions(database)
        try:
            store = SQLiteSessionStore(connection)
            session = make_session()
            store.open(session)
            # Standing in for the file having gone bad underneath an open
            # connection: the statement is the same one, and the table it
            # needs is not there.
            connection.execute("DROP TABLE work_actions")

            with pytest.raises(SessionStoreError):
                store.record_work_action(
                    WorkAction(
                        session=session.id,
                        recorded_at=session.started_at,
                        item="982",
                        verb="comment",
                        detail="",
                    )
                )
        finally:
            connection.close()

    def test_a_directory_that_cannot_be_made_is_translated(self, tmp_path: Path) -> None:
        # FileSessionStore creates the parent on the way in, which is the one
        # failure `open_sessions` never sees.
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("")
        store = FileSessionStore(blocker / "sessions" / "sessions.sqlite3")

        with pytest.raises(SessionStoreError):
            store.open(make_session())

    def test_a_caller_error_is_still_a_caller_error(self, tmp_path: Path) -> None:
        # The translation must not swallow the distinction. An id opened twice
        # is the caller confusing two sessions whatever the store is made of.
        connection = open_sessions(tmp_path / "sessions.sqlite3")
        try:
            store = SQLiteSessionStore(connection)
            session = make_session()
            store.open(session)

            with pytest.raises(SessionStateError):
                store.open(session)
        finally:
            connection.close()
