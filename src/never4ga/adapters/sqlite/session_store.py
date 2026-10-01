"""SessionStore over SQLite."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import (
    Checkpoint,
    GivenContext,
    Session,
    SessionStateError,
    WorkAction,
)
from never4ga.errors import SessionStoreError

__all__ = ["SQLiteSessionStore"]


def _instant(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


@contextmanager
def _store_failures(doing: str) -> Iterator[None]:
    """Translate the driver's failures at the boundary, per `errors`.

    Every caller of this store is entitled to catch something it is allowed to
    import, and `sqlite3` is not that -- a service that caught
    ``OperationalError`` would be reaching through the port at the one layer
    the layering test forbids it. So a locked database, a full disk, a failed
    migration and a corrupt file all arrive as :class:`SessionStoreError`.

    :class:`SessionStateError` is deliberately *not* translated. It says the
    caller asked for something incoherent, which is true whatever the store is
    made of, and ``open`` raises it from inside this block after reading the
    driver's ``IntegrityError``.
    """
    try:
        yield
    except sqlite3.Error as error:
        raise SessionStoreError(f"the session store could not {doing}: {error}") from error


class SQLiteSessionStore:
    """The session store, in the database :func:`open_sessions` opens."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def open(self, session: Session) -> None:
        with _store_failures("record an opened session"):
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO sessions
                            (id, workspace, actor, client, started_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(session.id),
                            str(session.workspace),
                            session.actor,
                            session.client,
                            session.started_at.isoformat(),
                            session.last_seen_at.isoformat(),
                        ),
                    )
            except sqlite3.IntegrityError as error:
                # The primary key is what refuses this, so the refusal is the
                # database's rather than a read-then-write race of our own.
                raise SessionStateError(
                    f"session {session.id} is already open; a session id is minted once per startup"
                ) from error

    def get(self, session: SessionId) -> Session | None:
        with _store_failures("read a session"):
            row = self._connection.execute(
                """
                SELECT id, workspace, actor, client, started_at, last_seen_at, wrapped_log
                FROM sessions WHERE id = ?
                """,
                (str(session),),
            ).fetchone()
            return None if row is None else _session_of(row)

    def record_work_action(self, action: WorkAction) -> None:
        with _store_failures("record a tracker write"), self._connection:
            updated = self._connection.execute(
                """
                    UPDATE sessions
                       SET last_seen_at = MAX(last_seen_at, ?)
                     WHERE id = ?
                    """,
                (action.recorded_at.isoformat(), str(action.session)),
            ).rowcount
            if not updated:
                raise SessionStateError(
                    f"session {action.session} was never opened; "
                    "an action belonging to nothing can never be reconciled"
                )
            self._connection.execute(
                """
                    INSERT INTO work_actions (session, recorded_at, item, verb, detail)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                (
                    str(action.session),
                    action.recorded_at.isoformat(),
                    action.item,
                    action.verb,
                    action.detail,
                ),
            )

    def work_actions(self, session: SessionId) -> Sequence[WorkAction]:
        with _store_failures("read a session's tracker writes"):
            rows = self._connection.execute(
                """
                SELECT recorded_at, item, verb, detail
                  FROM work_actions
                 WHERE session = ?
                 ORDER BY recorded_at, rowid
                """,
                (str(session),),
            ).fetchall()
            return tuple(
                WorkAction(
                    session=session,
                    recorded_at=datetime.fromisoformat(row["recorded_at"]),
                    item=row["item"],
                    verb=row["verb"],
                    detail=row["detail"],
                )
                for row in rows
            )

    def append(self, checkpoint: Checkpoint) -> None:
        with _store_failures("append a checkpoint"), self._connection:
            # One transaction for both writes: a checkpoint that landed while
            # its session's `last_seen_at` did not would make the session look
            # quieter than it is, and `recent` reads that column.
            updated = self._connection.execute(
                """
                    UPDATE sessions
                       SET last_seen_at = MAX(last_seen_at, ?)
                     WHERE id = ?
                    """,
                (checkpoint.recorded_at.isoformat(), str(checkpoint.session)),
            ).rowcount
            if not updated:
                raise SessionStateError(
                    f"session {checkpoint.session} was never opened; "
                    "a checkpoint that belongs to nothing cannot be wrapped"
                )
            self._connection.execute(
                """
                    INSERT INTO checkpoints
                        (session, recorded_at, note, actions, decisions, memories, work, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    str(checkpoint.session),
                    checkpoint.recorded_at.isoformat(),
                    checkpoint.note,
                    json.dumps(list(checkpoint.actions)),
                    json.dumps(list(checkpoint.decisions)),
                    json.dumps(list(checkpoint.memories)),
                    json.dumps(list(checkpoint.work)),
                    json.dumps(list(checkpoint.context)),
                ),
            )

    def record_given_context(self, given: Sequence[GivenContext]) -> None:
        with _store_failures("record what a startup gave a session"), self._connection:
            for one in given:
                known = self._connection.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (str(one.session),)
                ).fetchone()
                if known is None:
                    raise SessionStateError(
                        f"session {one.session} was never opened; "
                        "what it was given cannot be recorded against nothing"
                    )
                # `OR IGNORE` against the UNIQUE constraint is what makes the
                # first record win, in the statement rather than in a
                # read-then-write two clients could interleave.
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO given_context
                        (session, concept_id, path, title, digest)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(one.session), str(one.concept_id), str(one.path), one.title, one.digest),
                )

    def given_context(self, session: SessionId) -> Sequence[GivenContext]:
        with _store_failures("read what a startup gave a session"):
            rows = self._connection.execute(
                """
                SELECT concept_id, path, title, digest
                  FROM given_context
                 WHERE session = ?
                 ORDER BY sequence
                """,
                (str(session),),
            ).fetchall()
            return tuple(
                GivenContext(
                    session=session,
                    concept_id=ConceptId.parse(row[0]),
                    path=VaultPath.parse(row[1]),
                    title=row[2],
                    digest=row[3],
                )
                for row in rows
            )

    def mark_wrapped(self, session: SessionId, log: ConceptId) -> None:
        with _store_failures("mark a session wrapped"), self._connection:
            # `WHERE wrapped_log IS NULL` is what makes the first mark win,
            # in the statement rather than in a read-then-write that two
            # clients could interleave.
            updated = self._connection.execute(
                "UPDATE sessions SET wrapped_log = ? WHERE id = ? AND wrapped_log IS NULL",
                (str(log), str(session)),
            ).rowcount
            if not updated and self.get(session) is None:
                raise SessionStateError(f"session {session} was never opened")

    def checkpoints(self, session: SessionId) -> Sequence[Checkpoint]:
        with _store_failures("read a session's checkpoints"):
            rows = self._connection.execute(
                """
                SELECT session, recorded_at, note, actions, decisions, memories, work, context
                FROM checkpoints WHERE session = ?
                ORDER BY recorded_at, sequence
                """,
                (str(session),),
            ).fetchall()
            return tuple(
                Checkpoint(
                    session=SessionId.parse(row[0]),
                    recorded_at=_instant(row[1]),
                    note=row[2],
                    actions=tuple(json.loads(row[3])),
                    decisions=tuple(json.loads(row[4])),
                    memories=tuple(json.loads(row[5])),
                    work=tuple(json.loads(row[6])),
                    context=tuple(json.loads(row[7])),
                )
                for row in rows
            )

    def recent(
        self, workspace: ConceptId, *, limit: int = 10, before: datetime | None = None
    ) -> Sequence[Session]:
        with _store_failures("read a workspace's recent sessions"):
            rows = self._connection.execute(
                """
                SELECT id, workspace, actor, client, started_at, last_seen_at, wrapped_log
                FROM sessions
                WHERE workspace = ? AND (? IS NULL OR last_seen_at < ?)
                ORDER BY last_seen_at DESC, id DESC
                LIMIT ?
                """,
                (
                    str(workspace),
                    None if before is None else before.isoformat(),
                    None if before is None else before.isoformat(),
                    limit,
                ),
            ).fetchall()
            return tuple(_session_of(row) for row in rows)


def _session_of(row: tuple[str, str, str, str | None, str, str, str | None]) -> Session:
    return Session(
        id=SessionId.parse(row[0]),
        workspace=ConceptId.parse(row[1]),
        actor=row[2],
        client=row[3],
        started_at=_instant(row[4]),
        last_seen_at=_instant(row[5]),
        wrapped_log=None if row[6] is None else ConceptId.parse(row[6]),
    )
