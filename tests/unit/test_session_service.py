"""Checkpoints accumulate outside the vault."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from never4ga.adapters.fakes.session_store import InMemorySessionStore
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import Session
from never4ga.errors import Never4gaError
from never4ga.services.sessions import SessionService

START = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
WORKSPACE = ConceptId.new()


class _Clock:
    """A clock that advances a minute each time it is read."""

    def __init__(self) -> None:
        self._ticks = 0

    def __call__(self) -> datetime:
        self._ticks += 1
        return START + timedelta(minutes=self._ticks)


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def service(store: InMemorySessionStore) -> SessionService:
    return SessionService(store, now=_Clock())


@pytest.fixture
def session(store: InMemorySessionStore) -> Session:
    opened = Session.opened(
        workspace=WORKSPACE, actor="claude-code/claude-opus-5", started_at=START
    )
    store.open(opened)
    return opened


class TestRecordingACheckpoint:
    def test_it_is_stamped_and_kept(self, service: SessionService, session: Session) -> None:
        recorded = service.checkpoint(session.id, "did a thing")
        assert recorded.note == "did a thing"
        assert [c.note for c in service.checkpoints(session.id)] == ["did a thing"]

    def test_repeated_checkpoints_accumulate_in_order(
        self, service: SessionService, session: Session
    ) -> None:
        for index in range(4):
            service.checkpoint(session.id, f"step {index}")
        assert [c.note for c in service.checkpoints(session.id)] == [
            f"step {index}" for index in range(4)
        ]

    def test_actions_are_kept_apart_from_the_note(
        self, service: SessionService, session: Session
    ) -> None:
        # A summary is prose; an action is a fact. Merging them would make the
        # log wrap writes unable to tell one from the other.
        recorded = service.checkpoint(session.id, "cleared two defects", actions=("merged #37",))
        assert recorded.actions == ("merged #37",)

    def test_an_unknown_session_is_refused_rather_than_invented(
        self, service: SessionService
    ) -> None:
        with pytest.raises(Never4gaError, match="never opened"):
            service.checkpoint(SessionId.new(), "orphan")

    def test_an_empty_note_is_refused(self, service: SessionService, session: Session) -> None:
        with pytest.raises(Never4gaError):
            service.checkpoint(session.id, "   ")

    def test_checkpointing_advances_the_session(
        self, service: SessionService, store: InMemorySessionStore, session: Session
    ) -> None:
        service.checkpoint(session.id, "still here")
        stored = store.get(session.id)
        assert stored is not None
        assert stored.last_seen_at > session.started_at


class TestOpeningASession:
    def test_it_records_who_and_where(
        self, service: SessionService, store: InMemorySessionStore
    ) -> None:
        opened = service.open(workspace=WORKSPACE, actor="codex/gpt-5", client="codex")
        stored = store.get(opened.id)
        assert stored is not None
        assert (stored.workspace, stored.actor, stored.client) == (
            WORKSPACE,
            "codex/gpt-5",
            "codex",
        )

    def test_each_open_is_a_new_session(self, service: SessionService) -> None:
        first = service.open(workspace=WORKSPACE, actor="a/b")
        second = service.open(workspace=WORKSPACE, actor="a/b")
        assert first.id != second.id


class TestRecentSessions:
    def test_the_most_recently_active_comes_first(
        self, service: SessionService, store: InMemorySessionStore
    ) -> None:
        older = service.open(workspace=WORKSPACE, actor="a/b")
        newer = service.open(workspace=WORKSPACE, actor="a/b")
        service.checkpoint(older.id, "still working")
        assert next(s.id for s in service.recent(WORKSPACE)) == older.id
        assert newer.id in {s.id for s in service.recent(WORKSPACE)}
