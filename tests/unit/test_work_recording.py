"""A tracker write is recorded against its session, whichever surface made it.

`wrap` weighs what a session *declared* (`checkpoint --work`) against what it
was *observed* doing, and the observation is this record. If one surface kept
it alone, an agent working through another could comment on every ticket it
declared and still be told at wrap that it had written to none of them.

So deciding *which* item a write touched, and the recording itself, is a
service the three surfaces share. `core/05` section 13 says no interface owns
separate business logic, and the contract's operation metadata asks every
mutating operation to accept a ``session_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.sqlite.session_store import SQLiteSessionStore
from never4ga.adapters.sqlite.sessions import open_sessions
from never4ga.composition import FileSessionStore
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import WorkAction
from never4ga.errors import SessionStoreError, StructuredError
from never4ga.services.sessions import SessionService
from never4ga.services.work_recording import (
    TrackerWriteRecord,
    noting_a_lost_record,
    record_tracker_write,
    written_item,
)

WORKSPACE = ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbb6")


def clock() -> datetime:
    return datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteSessionStore:
    return SQLiteSessionStore(open_sessions(tmp_path / "sessions.sqlite3"))


@pytest.fixture
def session(store: SQLiteSessionStore) -> SessionId:
    return SessionService(store, now=clock).open(workspace=WORKSPACE, actor="claude-code").id


class TestWhichItemAWriteTouched:
    """A create has no item argument, so it has to name itself.

    `update` and `comment` are given the item; `create` is not, and the new id
    exists only in the tracker's reply. Reading the argument alone would record
    nothing for a create.
    """

    def test_an_update_names_the_item_it_was_given(self) -> None:
        assert written_item({"applied": True}, given="994", applied=True) == "994"

    def test_a_create_names_the_item_the_tracker_returned(self) -> None:
        # The only place the new id exists is the reply.
        result = {"applied": True, "item": {"ref": "1005", "url": "https://pm/1005"}}
        assert written_item(result, given=None, applied=True) == "1005"

    def test_the_item_the_tracker_returned_wins_over_the_one_given(self) -> None:
        result = {"applied": True, "item": {"ref": "1005"}}
        assert written_item(result, given="994", applied=True) == "1005"

    def test_a_write_that_sent_nothing_names_nothing(self) -> None:
        # Without apply the tracker was never asked. Recording an intention is
        # what the whole declaration/action split exists to prevent.
        result = {"applied": False, "proposal": "would create"}
        assert written_item(result, given="994", applied=False) is None

    def test_a_reply_without_an_item_names_nothing(self) -> None:
        assert written_item({"applied": True}, given=None, applied=True) is None

    def test_a_structured_error_names_nothing(self) -> None:
        assert written_item(StructuredError("nope", "nope"), given="994", applied=True) is None


class TestTheWriteIsRecorded:
    def test_an_applied_write_is_recorded_against_its_session(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        record_tracker_write(
            store,
            str(session),
            verb="comment",
            result={"applied": True, "url": "https://pm/982"},
            given="982",
            applied=True,
        )

        [action] = store.work_actions(session)
        assert (action.item, action.verb, action.detail) == ("982", "comment", "https://pm/982")

    def test_it_clears_the_item_from_what_wrap_reports(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        sessions = SessionService(store, now=clock)
        sessions.checkpoint(session, note="commented", work=("982: waiting on step 8",))

        record_tracker_write(
            store, str(session), verb="comment", result={"applied": True}, given="982", applied=True
        )

        assert sessions.outstanding_work(session) == ()

    def test_the_detail_names_the_fields_an_update_changed(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        record_tracker_write(
            store,
            str(session),
            verb="update",
            result={"applied": True, "changed": {"status": "Closed", "priority": "Low"}},
            given="840",
            applied=True,
        )

        [action] = store.work_actions(session)
        assert action.detail == "priority=Low, status=Closed"


class TestNothingIsRecordedWithoutAnObservation:
    def test_no_session_records_nothing(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        record_tracker_write(
            store, None, verb="comment", result={"applied": True}, given="982", applied=True
        )

        assert store.work_actions(session) == ()

    def test_an_unapplied_write_records_nothing(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        record_tracker_write(
            store,
            str(session),
            verb="comment",
            result={"applied": False},
            given="982",
            applied=False,
        )

        assert store.work_actions(session) == ()

    def test_no_store_records_nothing(self) -> None:
        # Not a vault: there is nowhere to record, and nothing to fail.
        record_tracker_write(
            None, "anything", verb="comment", result={"applied": True}, given="982", applied=True
        )


class TestRecordingNeverFailsTheWrite:
    """By the time this runs the tracker has already changed.

    A session bookkeeping problem must not be reported as though somebody's
    ticket did not update, so a session that cannot take the record is passed
    over rather than raised.
    """

    def test_a_malformed_session_id_is_passed_over(self, store: SQLiteSessionStore) -> None:
        record_tracker_write(
            store,
            "not-a-session",
            verb="comment",
            result={"applied": True},
            given="982",
            applied=True,
        )

    def test_a_session_database_that_was_never_opened_is_passed_over(self, tmp_path: Path) -> None:
        missing = FileSessionStore(tmp_path / "nowhere" / "sessions.sqlite3")
        record_tracker_write(
            missing,
            "01a0c1be-f64a-76b0-8c96-bff5b4a95382",
            verb="comment",
            result={"applied": True},
            given="982",
            applied=True,
        )


class _BrokenStore:
    """A session store that is there and cannot be used.

    What `open_sessions` and :class:`SQLiteSessionStore` raise when the
    file is locked past its busy timeout, the disk is full, a migration fails
    or ``sessions.sqlite3`` is corrupt. Standing in for all four, because what
    matters to this service is only that the port raised its declared error.
    """

    def __init__(self, message: str = "database is locked") -> None:
        self.message = message

    def record_work_action(self, action: WorkAction) -> None:
        raise SessionStoreError(self.message)

    def __getattr__(self, name: str) -> object:
        raise SessionStoreError(self.message)


class TestAStoreThatCannotTakeTheRecord:
    """The tracker has already changed; only the bookkeeping failed.

    The write landed either way, so the caller must not be told the ticket did
    not change. Through MCP an agent would then retry, turning one comment into
    two.
    """

    def test_the_write_is_not_failed(self, session: SessionId) -> None:
        record = record_tracker_write(
            _BrokenStore(),  # type: ignore[arg-type]
            str(session),
            verb="comment",
            result={"applied": True},
            given="982",
            applied=True,
        )

        assert record.recorded is False

    def test_the_loss_is_reported_rather_than_swallowed(self, session: SessionId) -> None:
        record = record_tracker_write(
            _BrokenStore("disk I/O error"),  # type: ignore[arg-type]
            str(session),
            verb="create",
            result={"applied": True, "item": {"ref": "1300"}},
            given=None,
            applied=True,
        )

        assert record.lost is not None
        assert "disk I/O error" in record.lost

    def test_a_session_that_cannot_take_it_is_reported_too(self, store: SQLiteSessionStore) -> None:
        # Silence here would let `wrap` call the item outstanding with nothing
        # to say why.
        record = record_tracker_write(
            store,
            "not-a-session",
            verb="comment",
            result={"applied": True},
            given="982",
            applied=True,
        )

        assert record.lost is not None

    def test_nothing_to_record_is_not_a_loss(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        # The ordinary case. A proposal wrote nothing, so nothing was lost,
        # and a surface must not warn about it.
        record = record_tracker_write(
            store,
            str(session),
            verb="comment",
            result={"applied": False},
            given="982",
            applied=False,
        )

        assert (record.recorded, record.lost) == (False, None)

    def test_a_successful_record_reports_no_loss(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        record = record_tracker_write(
            store,
            str(session),
            verb="comment",
            result={"applied": True},
            given="982",
            applied=True,
        )

        assert record.recorded is True
        assert record.lost is None


class TestThePayloadSaysSo:
    """The one annotation all three surfaces put on the write they return."""

    def test_a_lost_record_is_named_on_the_payload(self, session: SessionId) -> None:
        record = record_tracker_write(
            _BrokenStore(),  # type: ignore[arg-type]
            str(session),
            verb="comment",
            result={"applied": True},
            given="982",
            applied=True,
        )

        payload = noting_a_lost_record({"applied": True}, record)

        assert payload["record_lost"] == record.lost

    def test_an_ordinary_write_is_left_exactly_as_it_was(
        self, store: SQLiteSessionStore, session: SessionId
    ) -> None:
        # Additive and absent: a reader that does not know the key sees the
        # shape it has always seen.
        result = {"applied": True, "url": "https://pm/982"}
        record = record_tracker_write(
            store, str(session), verb="comment", result=result, given="982", applied=True
        )

        assert noting_a_lost_record(result, record) == result

    def test_a_structured_error_is_left_alone(self) -> None:
        failure = StructuredError("nope", "nope")
        assert noting_a_lost_record(failure, TrackerWriteRecord()) is failure
