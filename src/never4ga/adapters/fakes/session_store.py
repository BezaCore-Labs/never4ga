"""An in-memory SessionStore, for tests and for a caller that wants no file."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import (
    Checkpoint,
    GivenContext,
    Session,
    SessionStateError,
    WorkAction,
)

__all__ = ["InMemorySessionStore"]


class InMemorySessionStore:
    """Everything a real store does, in dictionaries."""

    def __init__(self) -> None:
        self._sessions: dict[SessionId, Session] = {}
        # Insertion order is the tie-break the contract asks for, and a list
        # per session gives it for free.
        self._checkpoints: dict[SessionId, list[Checkpoint]] = {}
        self._work_actions: dict[SessionId, list[WorkAction]] = {}
        self._given: dict[SessionId, list[GivenContext]] = {}

    def open(self, session: Session) -> None:
        if session.id in self._sessions:
            raise SessionStateError(
                f"session {session.id} is already open; a session id is minted once per startup"
            )
        self._sessions[session.id] = session
        self._checkpoints[session.id] = []
        self._work_actions[session.id] = []
        self._given[session.id] = []

    def get(self, session: SessionId) -> Session | None:
        return self._sessions.get(session)

    def append(self, checkpoint: Checkpoint) -> None:
        session = self._sessions.get(checkpoint.session)
        if session is None:
            raise SessionStateError(
                f"session {checkpoint.session} was never opened; "
                "a checkpoint that belongs to nothing cannot be wrapped"
            )
        self._checkpoints[checkpoint.session].append(checkpoint)
        self._sessions[checkpoint.session] = session.seen(checkpoint.recorded_at)

    def record_work_action(self, action: WorkAction) -> None:
        session = self._sessions.get(action.session)
        if session is None:
            raise SessionStateError(
                f"session {action.session} was never opened; "
                "an action belonging to nothing can never be reconciled"
            )
        self._work_actions[action.session].append(action)
        self._sessions[action.session] = session.seen(action.recorded_at)

    def work_actions(self, session: SessionId) -> Sequence[WorkAction]:
        return tuple(self._work_actions.get(session, ()))

    def record_given_context(self, given: Sequence[GivenContext]) -> None:
        for one in given:
            if one.session not in self._sessions:
                raise SessionStateError(
                    f"session {one.session} was never opened; "
                    "what it was given cannot be recorded against nothing"
                )
            recorded = self._given[one.session]
            if all(existing.concept_id != one.concept_id for existing in recorded):
                recorded.append(one)

    def given_context(self, session: SessionId) -> Sequence[GivenContext]:
        return tuple(self._given.get(session, ()))

    def mark_wrapped(self, session: SessionId, log: ConceptId) -> None:
        existing = self._sessions.get(session)
        if existing is None:
            raise SessionStateError(f"session {session} was never opened")
        if existing.wrapped_log is None:
            self._sessions[session] = replace(existing, wrapped_log=log)

    def checkpoints(self, session: SessionId) -> Sequence[Checkpoint]:
        # Stable sort, so equal instants keep the order they arrived in.
        return tuple(sorted(self._checkpoints.get(session, ()), key=lambda c: c.recorded_at))

    def recent(
        self, workspace: ConceptId, *, limit: int = 10, before: datetime | None = None
    ) -> Sequence[Session]:
        matching = [
            session
            for session in self._sessions.values()
            if session.workspace == workspace and (before is None or session.last_seen_at < before)
        ]
        matching.sort(key=lambda session: session.last_seen_at, reverse=True)
        return tuple(matching[:limit])
