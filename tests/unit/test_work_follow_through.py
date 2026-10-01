"""A declared work item is followed through before a session may wrap quietly.

`wrap` writes nothing to a tracker. A ticket either needs some particular
information, in which case somebody should write that, or it does not, in which
case "a session wrapped" is noise on somebody's audit trail.

The somebody is the agent: it holds the conversation, it knows what changed,
and `work update` / `work comment` are already gated by `sync_policy` and
`--apply`. Otherwise a person working through agents would have to reconcile a
tracker by hand.

`wrap` enforces rather than writes. These tests pin the difference between two
facts:

- a **declaration** (`checkpoint --work`) is a claim that something may need
  doing, and Never4gA records it verbatim without believing it;
- an **action** is a write Never4gA performed itself, so it is observed rather
  than claimed.

`wrap` compares the two and refuses to pass over the gap in silence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.sqlite.session_store import SQLiteSessionStore
from never4ga.adapters.sqlite.sessions import open_sessions
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.services.sessions import SessionService

WORKSPACE = ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbb6")


def clock() -> datetime:
    return datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sessions(tmp_path: Path) -> SessionService:
    connection = open_sessions(tmp_path / "sessions.sqlite3")
    return SessionService(SQLiteSessionStore(connection), now=clock)


@pytest.fixture
def session(sessions: SessionService) -> SessionId:
    return sessions.open(workspace=WORKSPACE, actor="claude-code").id


class TestAnActionIsObservedNotClaimed:
    def test_a_recorded_action_names_the_item_and_the_verb(
        self, sessions: SessionService, session: SessionId
    ) -> None:
        sessions.record_work_action(session, item="840", verb="update", detail="status=Closed")

        actions = sessions.work_actions(session)

        assert [(a.item, a.verb) for a in actions] == [("840", "update")]

    def test_actions_belong_to_their_own_session(
        self, sessions: SessionService, session: SessionId
    ) -> None:
        other = sessions.open(workspace=WORKSPACE, actor="codex").id
        sessions.record_work_action(session, item="840", verb="comment", detail="why")

        assert sessions.work_actions(other) == ()


class TestWrapSeesTheGap:
    def test_a_declared_item_with_no_action_is_outstanding(
        self, sessions: SessionService, session: SessionId
    ) -> None:
        sessions.checkpoint(session, note="did the thing", work=("840: ready to close",))

        assert sessions.outstanding_work(session) == ("840",)

    def test_acting_on_it_clears_it(self, sessions: SessionService, session: SessionId) -> None:
        sessions.checkpoint(session, note="did the thing", work=("840: ready to close",))
        sessions.record_work_action(session, item="840", verb="update", detail="status=Closed")

        assert sessions.outstanding_work(session) == ()

    def test_an_item_declared_twice_is_reported_once(
        self, sessions: SessionService, session: SessionId
    ) -> None:
        sessions.checkpoint(session, note="first", work=("840: ready to close",))
        sessions.checkpoint(session, note="second", work=("840: still ready",))

        assert sessions.outstanding_work(session) == ("840",)

    def test_a_declaration_that_names_no_item_is_not_outstanding(
        self, sessions: SessionService, session: SessionId
    ) -> None:
        """`--work` is free text; only a leading id is a thing to act on.

        Never4gA does not parse intent out of prose. A declaration it cannot
        resolve to an item is still reported at wrap as a candidate, but `wrap`
        cannot claim it went unactioned.
        """
        sessions.checkpoint(session, note="did the thing", work=("somebody should look at PM",))

        assert sessions.outstanding_work(session) == ()

    def test_nothing_declared_means_nothing_outstanding(
        self, sessions: SessionService, session: SessionId
    ) -> None:
        sessions.checkpoint(session, note="a session that touched no ticket")

        assert sessions.outstanding_work(session) == ()
