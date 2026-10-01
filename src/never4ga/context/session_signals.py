"""What one client's session tells another.

A wrapped session is already in a pack as its ``activity_log``, found by
``RECENT_ACTIVITY`` in the structural stage. The ones that have not been
wrapped yet are invisible, and they are the interesting ones: they are what is
happening *now*, in whatever tool the person was using an hour ago.

Retrieval is by workspace and recency rather than by session id, because two
tools on one machine have no channel to pass an id through, and "what happened
here recently" is the question actually being asked. So a Codex session opened
after a Claude one sees the Claude work without either tool knowing the other
exists.

Signals rather than items, as with tracker state: a checkpoint is local
operational state, not a canonical document, and the pack's shape does not
change when they arrive.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from never4ga.domain.context import ContextDepth, ContextRequest
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope
from never4ga.domain.signals import MAX_SIGNAL_TEXT_LENGTH, ContextSignal, SignalValue
from never4ga.ports.session_store import SessionStore

__all__ = ["SESSION_LIMIT", "SessionSignalProvider"]

#: How many recent sessions reach a pack. A startup pack is bounded by design
#: (`core/07`), and a machine that has run for a year must not push a year of
#: sessions into one. Five is enough to answer "what happened here recently"
#: and small enough that it never dominates a budget.
SESSION_LIMIT: Final = 5

#: How many checkpoints of each session are carried. The last few say what a
#: session was doing; the whole narrative is what `wrap` writes to the vault.
CHECKPOINT_LIMIT: Final = 3


class SessionSignalProvider:
    """Recent unwrapped sessions in this workspace, whoever opened them."""

    provider_id = "sessions"

    def __init__(self, store: SessionStore, *, limit: int = SESSION_LIMIT) -> None:
        self._store = store
        self._limit = limit

    def supports(self, request: ContextRequest) -> bool:
        """Startup only.

        `core/04` section 34: startup runs once per session and focused
        retrieval many times within it. Repeating "what happened here recently"
        on every mid-session retrieval spends budget on an answer the caller
        already has.
        """
        return request.depth is ContextDepth.STARTUP

    def collect(self, request: ContextRequest, scope: ResolvedScope) -> Sequence[ContextSignal]:
        entries: list[SignalValue] = []
        for session in self._store.recent(scope.workspace_id, limit=self._limit):
            if session.is_wrapped:
                # Already in the pack as its `activity_log`. Saying it twice
                # spends budget to repeat itself.
                continue
            checkpoints = self._store.checkpoints(session.id)
            if not checkpoints:
                # A session that opened and said nothing is not news.
                continue
            entries.append(
                {
                    "session": str(session.id),
                    "client": session.client or "unstated",
                    "actor": session.actor,
                    "last_seen": session.last_seen_at.isoformat(),
                    "checkpoints": [
                        _clip(checkpoint.note) for checkpoint in checkpoints[-CHECKPOINT_LIMIT:]
                    ],
                }
            )
        if not entries:
            return ()
        return (
            ContextSignal(
                provider_id=self.provider_id,
                kind="session.recent",
                value=entries,
                reason=AcquisitionReason.of(
                    ReasonCode.RECENT_ACTIVITY,
                    detail="unwrapped sessions in this workspace",
                ),
            ),
        )


def _clip(note: str) -> str:
    """A checkpoint note, bounded like every other signal's text."""
    first = note.strip().splitlines()[0] if note.strip() else ""
    if len(first) <= MAX_SIGNAL_TEXT_LENGTH:
        return first
    return f"{first[: MAX_SIGNAL_TEXT_LENGTH - 1].rstrip()}…"
