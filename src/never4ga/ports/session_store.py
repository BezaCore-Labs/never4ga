"""SessionStore -- working session state, and the checkpoints inside it.

`core/08` section 1 keeps working state in "local runtime state" and episodic
memory in ``Workspace/Logs/``. This is the first half. `never4ga wrap` reads it
and writes the second.

**It is machine-local durable state, not derived state.** Every other backing
store Never4gA keeps can be deleted and rebuilt from Markdown -- that is what
``rebuild`` means, and `core/06` section 3 turns it into an invariant. A
checkpoint cannot be rebuilt from anything, because nothing in the vault ever
held it. So this store belongs beside ``workspaces.json`` among what should be
backed up, and ``rebuild`` must leave it alone.

It has its own database rather than a table in the index, for the same reason
the tracker cache does: a store with a different lifecycle should not make
``rebuild`` remember which tables to spare.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import Checkpoint, GivenContext, Session, WorkAction

__all__ = ["SessionStore"]


@runtime_checkable
class SessionStore(Protocol):
    """Working session state.

    **Every operation here may raise
    :class:`never4ga.errors.SessionStoreError`** when the store itself cannot
    be read or written -- locked, full, unmigratable, corrupt. An adapter
    translates its backend's failures into it at the boundary, because
    `services` may not import `adapters` and so has nothing else it is allowed
    to catch.

    That is a different thing from
    :class:`never4ga.domain.sessions.SessionStateError`, which several
    operations below raise: that one says the *caller* asked for something
    incoherent, and is true whatever the store is made of.

    What a lost write costs is the caller's to decide, not this port's.
    ``wrap`` must fail loudly rather than hold a session to account against a
    store it could not read. ``record_tracker_write`` must not, because by the
    time it runs somebody's ticket has already changed.
    """

    def open(self, session: Session) -> None:
        """Record a session that has just begun.

        Opening the same id twice is an error rather than an update: a session
        id is minted per startup, so a second open under one id means a
        caller has confused two sessions, and silently merging them would lose
        whichever it overwrote.
        """
        ...

    def get(self, session: SessionId) -> Session | None:
        """The session, or ``None``. An unknown id is a question, not a fault."""
        ...

    def append(self, checkpoint: Checkpoint) -> None:
        """Add a checkpoint and advance its session's ``last_seen_at``.

        Refuses a checkpoint for a session that was never opened: a checkpoint
        that belongs to nothing cannot be wrapped, and inventing the session to
        hold it would hide the caller's mistake.
        """
        ...

    def record_work_action(self, action: WorkAction) -> None:
        """Record a tracker write this session actually performed.

        Refuses an action for a session that was never opened, for the reason
        ``append`` does: an action belonging to nothing can never be reconciled
        against the declarations `wrap` reads.
        """
        ...

    def work_actions(self, session: SessionId) -> Sequence[WorkAction]:
        """Every tracker write recorded for one session, oldest first."""
        ...

    def record_given_context(self, given: Sequence[GivenContext]) -> None:
        """Record the context documents a startup pack carried (core/04 section 37).

        The first record of a document in a session wins: what the session was
        given is fixed when it opened, and a later record would move the
        baseline `wrap` compares a declaration against. Refuses a record for a
        session that was never opened, for the reason ``append`` does.
        """
        ...

    def given_context(self, session: SessionId) -> Sequence[GivenContext]:
        """What the session's startup carried, in the order it was recorded."""
        ...

    def mark_wrapped(self, session: SessionId, log: ConceptId) -> None:
        """Record the ``activity_log`` `wrap` wrote for this session.

        The first mark wins. Wrapping is idempotent, so a second wrap must find
        the same document to rewrite -- recording a second id would strand the
        first log in the vault with nothing pointing at it.
        """
        ...

    def checkpoints(self, session: SessionId) -> Sequence[Checkpoint]:
        """Every checkpoint of one session, oldest first.

        Ordered by when they were recorded, because `wrap` reads them as a
        narrative and a narrative has an order. Two recorded in the same instant
        keep the order they were appended in.
        """
        ...

    def recent(
        self, workspace: ConceptId, *, limit: int = 10, before: datetime | None = None
    ) -> Sequence[Session]:
        """The workspace's most recently seen sessions, newest first.

        This is how one client finds what another just did. It asks by
        workspace rather than by session id, because two tools on one machine
        have no channel to pass an id through -- and "what happened here
        recently" is the question actually being asked.
        """
        ...
