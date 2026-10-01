"""Working session state.

`core/08` section 1 places this layer precisely::

    Working     -> local runtime state
    Episodic    -> Workspace/Logs/

These are the *working* half. A session and its checkpoints live outside the
vault for as long as they are working state; what survives is the one
``activity_log`` that `never4ga wrap` writes, and that is the episodic record.

Deliberately not in :mod:`never4ga.domain.memory`, which is `core/08`'s external
memory contract -- augmentors whose results are candidates. Nothing here is a
candidate or a provider's opinion. It is what this machine did.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.errors import Never4gaError

#: The sentinel for "a caller did not say when this was last seen". A real
#: datetime cannot be used -- every instant is a plausible one -- and ``None``
#: would make the field optional for every reader that only ever sees it set.
UNSET_INSTANT: Final = datetime.min.replace(tzinfo=UTC)

__all__ = [
    "UNSET_INSTANT",
    "Checkpoint",
    "GivenContext",
    "Session",
    "SessionStateError",
    "WorkAction",
    "body_digest",
]


class SessionStateError(Never4gaError):
    """A session was asked for something its state does not allow."""


def _require_aware(when: datetime, field_name: str) -> None:
    """Every instant Never4gA writes itself carries an offset.

    A naive instant does not fail where it is created; it fails much later, when
    something subtracts it from an aware "now" and raises from inside a
    comparison. `core/02` section 5.2 wants times that mean something, and the
    cheapest way to get them is to refuse the ambiguous ones at the door.
    """
    if when.tzinfo is None:
        raise SessionStateError(f"{field_name} must carry a timezone")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One bounded record of what a session has done so far.

    A checkpoint never touches the vault. It accumulates here, and `wrap` is
    what turns the accumulation into an ``activity_log``.

    ``note`` is what the Skill chose to say. Never4gA does not read a client's
    transcript, so this is the whole of what it knows, and it is written by an
    agent summarising deliberately rather than by anything scraping a
    conversation.
    """

    session: SessionId
    recorded_at: datetime
    note: str
    #: Durable things that actually happened -- a merged PR, a written concept.
    #: Separate from the note because a summary is prose and these are facts.
    actions: tuple[str, ...] = ()
    #: What the agent says was decided, recorded verbatim.
    #:
    #: Never4gA cannot generate this: it makes no LLM call here, so it has only
    #: notes, actions and an identity, and none of those is judgement. The
    #: agent holds the conversation and a model, so the agent declares and
    #: Never4gA records the claim. Recording it is not endorsing it -- turning
    #: a proposal into a decision record is a step for a person.
    decisions: tuple[str, ...] = ()
    #: What the agent says is worth remembering beyond this session.
    memories: tuple[str, ...] = ()
    #: What the agent says a tracker work item may need, recorded and never
    #: acted on. `core/04` section 37's "external work changes", read the way it
    #: is written: *may need* an update is a candidate.
    #:
    #: `wrap` never posts a comment saying a session happened. A ticket either
    #: needs the information, in which case somebody should write that
    #: information, or it does not, in which case a note saying a session
    #: wrapped is noise on somebody's audit trail. Neither case wants a
    #: pointer to a vault path.
    work: tuple[str, ...] = ()
    #: Context documents the agent says this session changed something in
    #: (core/04 section 37). The one declaration `wrap` can check against the
    #: vault rather than a tracker: a declared document whose body never moved
    #: is reported, because saying a fact changed is not the same as correcting
    #: the document that asserts it.
    context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_aware(self.recorded_at, "a checkpoint's recorded_at")
        if not self.note.strip():
            raise SessionStateError("a checkpoint needs a note; an empty one records nothing")


def body_digest(body: str) -> str:
    """The digest `wrap` compares a context document's body by.

    The body and not the file, so editing a document's frontmatter -- pushing
    its `stale_after` forward, say -- does not count as correcting what it
    asserts.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GivenContext:
    """A context document a session's startup pack carried, as it stood then.

    core/04 section 37: every wrap lists these and asks whether the session
    changed anything they assert, and a declared one whose body still has this
    digest is reported as outstanding. Recorded whether the pack carried the body or
    only a reference to it -- a document too large for the budget still reached
    the session by name, and is the one this most needs to cover.

    Runtime state beside the checkpoints, not derived: nothing in the vault
    remembers what a given session was shown.
    """

    session: SessionId
    concept_id: ConceptId
    path: VaultPath
    title: str
    digest: str

    def __post_init__(self) -> None:
        if len(self.digest) != 64:
            raise SessionStateError("a given context document needs a sha256 digest of its body")


@dataclass(frozen=True, slots=True)
class WorkAction:
    """A write Never4gA actually performed against a tracker, for one session.

    The counterpart to :attr:`Checkpoint.work`, and deliberately a different
    kind of thing. A declaration is a claim an agent makes and Never4gA records
    without believing; an action is something Never4gA did itself, so it is
    observed. `wrap` can only hold a session to account for the gap between
    them because the two are not stored as one.

    Not derived state, for the same reason a checkpoint is not: the tracker
    knows a field changed, but nothing outside this database knows it changed
    *because of this session*.
    """

    session: SessionId
    recorded_at: datetime
    #: The tracker's own identifier, as a string -- providers disagree about
    #: whether an id is an integer, and `core/03` keeps them opaque.
    item: str
    #: Which verb ran: ``update`` or ``comment`` today.
    verb: str
    #: What it did, for the reader of a wrap. Never parsed.
    detail: str = ""

    def __post_init__(self) -> None:
        _require_aware(self.recorded_at, "a work action's recorded_at")
        if not self.item.strip():
            raise SessionStateError("a work action needs the item it acted on")


@dataclass(frozen=True, slots=True)
class Session:
    """One agent session against one workspace.

    Machine-local durable state, and **not** derived. Every other SQLite file
    Never4gA keeps can be deleted and rebuilt from Markdown; a checkpoint
    cannot, because nothing in the vault ever held it. It belongs with
    ``workspaces.json`` -- state worth backing up -- rather than with the
    index.
    """

    id: SessionId
    workspace: ConceptId
    actor: str
    started_at: datetime
    #: Which client opened it. Optional the way `ContextPack.client` is: a
    #: caller that did not say is recorded as not having said.
    client: str | None = None
    #: Advanced by each checkpoint, so "the most recent session here" can be
    #: answered without reading every checkpoint of every session. Required
    #: rather than defaulted: a store reading a row back knows it, and
    #: :meth:`opened` is what a caller starting a session uses.
    last_seen_at: datetime = UNSET_INSTANT
    #: The `activity_log` `wrap` wrote for this session, once it has. What
    #: makes wrapping idempotent: a second wrap rewrites this document instead
    #: of leaving two accounts of one session in the vault.
    wrapped_log: ConceptId | None = None

    def __post_init__(self) -> None:
        _require_aware(self.started_at, "a session's started_at")
        if self.last_seen_at is UNSET_INSTANT:
            object.__setattr__(self, "last_seen_at", self.started_at)
        _require_aware(self.last_seen_at, "a session's last_seen_at")
        if self.last_seen_at < self.started_at:
            raise SessionStateError("a session cannot have been seen before it started")
        if not self.actor.strip():
            raise SessionStateError("a session needs an actor; core/02 section 5.2 requires one")

    @classmethod
    def opened(
        cls,
        *,
        workspace: ConceptId,
        actor: str,
        started_at: datetime,
        client: str | None = None,
        session_id: SessionId | None = None,
    ) -> Session:
        """A session that has just begun, seen at the moment it started."""
        return cls(
            id=SessionId.new() if session_id is None else session_id,
            workspace=workspace,
            actor=actor,
            started_at=started_at,
            client=client,
            last_seen_at=started_at,
        )

    @property
    def is_wrapped(self) -> bool:
        return self.wrapped_log is not None

    def seen(self, when: datetime) -> Session:
        """The same session, observed again. Never moves backwards."""
        _require_aware(when, "a session's last_seen_at")
        return replace(self, last_seen_at=max(when, self.last_seen_at))
