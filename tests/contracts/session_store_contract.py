"""SessionStore contract.

core/06 section 22: every backend interface gets one reusable suite, and an
implementation becomes supported by passing it. A new backend subclasses this
and supplies a fixture; it does not restate the behaviour.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import Checkpoint, GivenContext, Session, SessionStateError
from never4ga.errors import Never4gaError
from never4ga.ports.session_store import SessionStore

WORKSPACE = ConceptId.new()
START = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def make_session(
    *,
    workspace: ConceptId = WORKSPACE,
    actor: str = "claude-code/claude-opus-5",
    started_at: datetime = START,
    client: str | None = "claude-code",
) -> Session:
    return Session.opened(workspace=workspace, actor=actor, started_at=started_at, client=client)


def make_checkpoint(
    session: Session,
    *,
    note: str = "did a thing",
    at: datetime | None = None,
    actions: tuple[str, ...] = (),
    decisions: tuple[str, ...] = (),
    memories: tuple[str, ...] = (),
    context: tuple[str, ...] = (),
) -> Checkpoint:
    return Checkpoint(
        session=session.id,
        recorded_at=at or session.started_at + timedelta(minutes=5),
        note=note,
        actions=actions,
        decisions=decisions,
        memories=memories,
        context=context,
    )


def make_given(
    session: Session, title: str = "Project State", digest: str = "a" * 64
) -> GivenContext:
    return GivenContext(
        session=session.id,
        concept_id=ConceptId.new(),
        path=VaultPath.parse(
            f"10_Workspaces/Never4gA/Context/{title.lower().replace(' ', '-')}.md"
        ),
        title=title,
        digest=digest,
    )


class SessionStoreContract:
    """Behaviour every SessionStore implementation must have."""

    @pytest.fixture
    def store(self) -> SessionStore:
        raise NotImplementedError

    # -- opening ----------------------------------------------------------

    def test_an_opened_session_comes_back(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        assert store.get(session.id) == session

    def test_an_unknown_session_is_none_rather_than_an_error(self, store: SessionStore) -> None:
        assert store.get(SessionId.new()) is None

    def test_opening_the_same_id_twice_is_refused(self, store: SessionStore) -> None:
        # A session id is minted per startup, so a second open under one id
        # means two sessions have been confused. Merging them silently would
        # lose whichever was overwritten.
        session = make_session()
        store.open(session)
        with pytest.raises(Never4gaError):
            store.open(session)

    def test_a_client_that_did_not_say_is_recorded_as_not_having_said(
        self, store: SessionStore
    ) -> None:
        session = make_session(client=None)
        store.open(session)
        stored = store.get(session.id)
        assert stored is not None
        assert stored.client is None

    # -- checkpoints ------------------------------------------------------

    def test_checkpoints_come_back_oldest_first(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        for minutes in (30, 10, 20):
            store.append(
                make_checkpoint(
                    session, note=f"at {minutes}", at=START + timedelta(minutes=minutes)
                )
            )
        assert [c.note for c in store.checkpoints(session.id)] == ["at 10", "at 20", "at 30"]

    def test_checkpoints_recorded_in_the_same_instant_keep_insertion_order(
        self, store: SessionStore
    ) -> None:
        session = make_session()
        store.open(session)
        for index in range(5):
            store.append(make_checkpoint(session, note=f"n{index}", at=START))
        assert [c.note for c in store.checkpoints(session.id)] == [f"n{i}" for i in range(5)]

    def test_actions_survive_the_round_trip(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        store.append(make_checkpoint(session, actions=("merged #38", "wrote a note")))
        (stored,) = store.checkpoints(session.id)
        assert stored.actions == ("merged #38", "wrote a note")

    def test_declarations_survive_the_round_trip(self, store: SessionStore) -> None:
        # The agent declares what was durable and Never4gA records the claim
        # verbatim. A store that reworded one would be interpreting it.
        session = make_session()
        store.open(session)
        store.append(
            make_checkpoint(
                session,
                decisions=("session ids are minted by Never4gA",),
                memories=("the daemon lags Actions by ~15 minutes",),
            )
        )
        (stored,) = store.checkpoints(session.id)
        assert stored.decisions == ("session ids are minted by Never4gA",)
        assert stored.memories == ("the daemon lags Actions by ~15 minutes",)

    def test_a_checkpoint_for_an_unopened_session_is_refused(self, store: SessionStore) -> None:
        # A checkpoint that belongs to nothing can never be wrapped, and
        # inventing a session to hold it would hide the caller's mistake.
        orphan = make_session()
        with pytest.raises(Never4gaError):
            store.append(make_checkpoint(orphan))

    def test_a_session_with_no_checkpoints_has_none(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        assert list(store.checkpoints(session.id)) == []

    def test_checkpoints_do_not_leak_between_sessions(self, store: SessionStore) -> None:
        first, second = make_session(), make_session()
        store.open(first)
        store.open(second)
        store.append(make_checkpoint(first, note="first's"))
        assert [c.note for c in store.checkpoints(second.id)] == []

    def test_appending_advances_last_seen(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        later = START + timedelta(hours=2)
        store.append(make_checkpoint(session, at=later))
        stored = store.get(session.id)
        assert stored is not None
        assert stored.last_seen_at == later

    def test_last_seen_never_moves_backwards(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        store.append(make_checkpoint(session, at=START + timedelta(hours=2)))
        store.append(make_checkpoint(session, at=START + timedelta(minutes=1)))
        stored = store.get(session.id)
        assert stored is not None
        assert stored.last_seen_at == START + timedelta(hours=2)

    # -- recency, which is how another client finds this ------------------

    def test_recent_returns_newest_first(self, store: SessionStore) -> None:
        for minutes in (0, 20, 10):
            session = make_session(started_at=START + timedelta(minutes=minutes))
            store.open(session)
        seen = [s.started_at for s in store.recent(WORKSPACE)]
        assert seen == sorted(seen, reverse=True)

    def test_recent_is_scoped_to_one_workspace(self, store: SessionStore) -> None:
        elsewhere = ConceptId.new()
        store.open(make_session())
        store.open(make_session(workspace=elsewhere))
        assert [s.workspace for s in store.recent(elsewhere)] == [elsewhere]

    def test_recent_respects_its_limit(self, store: SessionStore) -> None:
        for minutes in range(5):
            store.open(make_session(started_at=START + timedelta(minutes=minutes)))
        assert len(store.recent(WORKSPACE, limit=2)) == 2

    def test_recent_orders_by_last_seen_not_by_start(self, store: SessionStore) -> None:
        # A long session that is still active is more recent than a short one
        # that started later and stopped. "What happened here recently" means
        # activity, not birth order.
        old = make_session(started_at=START)
        new = make_session(started_at=START + timedelta(minutes=10))
        store.open(old)
        store.open(new)
        store.append(make_checkpoint(old, at=START + timedelta(hours=1)))
        assert next(s.id for s in store.recent(WORKSPACE)) == old.id

    def test_recent_can_look_before_an_instant(self, store: SessionStore) -> None:
        for minutes in (0, 30, 60):
            store.open(make_session(started_at=START + timedelta(minutes=minutes)))
        cutoff = START + timedelta(minutes=45)
        assert all(s.last_seen_at < cutoff for s in store.recent(WORKSPACE, before=cutoff))

    def test_a_workspace_with_no_sessions_is_empty_rather_than_an_error(
        self, store: SessionStore
    ) -> None:
        assert list(store.recent(ConceptId.new())) == []

    # -- wrapping, which must happen once ---------------------------------

    def test_a_fresh_session_is_not_wrapped(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        stored = store.get(session.id)
        assert stored is not None
        assert not stored.is_wrapped

    def test_marking_records_the_log_that_was_written(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        log = ConceptId.new()
        store.mark_wrapped(session.id, log)
        stored = store.get(session.id)
        assert stored is not None
        assert stored.wrapped_log == log

    def test_marking_twice_keeps_the_first_document(self, store: SessionStore) -> None:
        # Wrapping is idempotent, so a second wrap must find the same document
        # to rewrite. Recording a second id would strand the first log in the
        # vault with nothing pointing at it.
        session = make_session()
        store.open(session)
        first = ConceptId.new()
        store.mark_wrapped(session.id, first)
        store.mark_wrapped(session.id, ConceptId.new())
        stored = store.get(session.id)
        assert stored is not None
        assert stored.wrapped_log == first

    def test_marking_an_unopened_session_is_refused(self, store: SessionStore) -> None:
        with pytest.raises(Never4gaError):
            store.mark_wrapped(SessionId.new(), ConceptId.new())

    def test_wrapping_does_not_stop_a_later_checkpoint(self, store: SessionStore) -> None:
        # A session can carry on after it was wrapped; the next wrap rewrites
        # the same document. Refusing here would make an early wrap a mistake
        # a person could not undo.
        session = make_session()
        store.open(session)
        store.mark_wrapped(session.id, ConceptId.new())
        store.append(make_checkpoint(session, note="one more thing"))
        assert [c.note for c in store.checkpoints(session.id)] == ["one more thing"]

    # -- cross-client, which is the point ---------------------------------

    def test_one_clients_session_is_visible_to_another(self, store: SessionStore) -> None:
        written_by_claude = make_session(client="claude-code")
        store.open(written_by_claude)
        store.append(make_checkpoint(written_by_claude, note="fixed the index"))

        # Codex asks the same question a startup asks: what happened here?
        (found,) = store.recent(WORKSPACE)
        assert found.client == "claude-code"
        assert [c.note for c in store.checkpoints(found.id)] == ["fixed the index"]

    # -- what a startup gave the session (core/04 section 37) -------------

    def test_a_context_declaration_survives_the_round_trip(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        store.append(make_checkpoint(session, context=("01a0: the rebrand is not done",)))
        (stored,) = store.checkpoints(session.id)
        assert stored.context == ("01a0: the rebrand is not done",)

    def test_what_startup_gave_comes_back_in_order(self, store: SessionStore) -> None:
        session = make_session()
        store.open(session)
        given = (make_given(session, "Start Here"), make_given(session, "Project State"))
        store.record_given_context(given)
        assert tuple(store.given_context(session.id)) == given

    def test_the_first_record_of_a_document_wins(self, store: SessionStore) -> None:
        # What the session was given is fixed at startup. A second record of
        # the same document would move the baseline a wrap compares against.
        session = make_session()
        store.open(session)
        first = make_given(session, digest="a" * 64)
        store.record_given_context((first,))
        store.record_given_context((replace(first, digest="b" * 64),))
        assert tuple(store.given_context(session.id)) == (first,)

    def test_given_context_is_per_session(self, store: SessionStore) -> None:
        one, other = make_session(), make_session()
        store.open(one)
        store.open(other)
        store.record_given_context((make_given(one),))
        assert tuple(store.given_context(other.id)) == ()

    def test_given_context_for_an_unopened_session_is_refused(self, store: SessionStore) -> None:
        orphan = make_session()
        with pytest.raises(SessionStateError):
            store.record_given_context((make_given(orphan),))

    def test_an_unknown_session_was_given_nothing(self, store: SessionStore) -> None:
        assert tuple(store.given_context(SessionId.new())) == ()
