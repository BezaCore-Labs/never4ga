"""Recording a tracker write against the session that made it.

`wrap` weighs a session's *declarations* (`checkpoint --work`) against the
writes it was *observed* making, and this is where the observation is taken.
Every surface has to take it: otherwise an agent working through MCP could act
on every item it declared and still be told at wrap that it had written to none
of them.

A service, then, for the reason `services/work_writing.py` gives for being
one: `core/05` section 13 makes the CLI, the API and MCP thin clients over the
same application services, and the contract's operation metadata asks every
mutating operation to accept a ``session_id``. Each surface supplies the
session store and what the caller said; this decides what, if anything, was
written, and records it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from never4ga.domain.identity import SessionId
from never4ga.domain.sessions import SessionStateError, WorkAction
from never4ga.errors import SessionStoreError, StructuredError
from never4ga.ports.session_store import SessionStore
from never4ga.services.sessions import SessionService

__all__ = [
    "TrackerWriteRecord",
    "noting_a_lost_record",
    "record_tracker_write",
    "write_detail",
    "written_item",
]


@dataclass(frozen=True, slots=True)
class TrackerWriteRecord:
    """What became of the attempt to record a tracker write against a session.

    Three outcomes, and the caller needs all three apart. *Nothing to
    record* is the ordinary case: no session was given, the write was a
    proposal, or the tracker refused it. *Recorded* is the observation `wrap`
    will weigh. *Lost* is a write that really happened and that this session
    will not be able to prove it made.

    Returning the third rather than raising it is the whole point. The tracker
    has already changed by the time the record is attempted, so raising would
    report somebody's ticket as unchanged because bookkeeping failed -- and
    swallowing it silently would leave `wrap` calling the item outstanding
    with nothing to say why.
    """

    action: WorkAction | None = None
    lost: str | None = None

    def __post_init__(self) -> None:
        if self.action is not None and self.lost is not None:
            raise ValueError("a record was either taken or lost, never both")

    @property
    def recorded(self) -> bool:
        return self.action is not None


def written_item(result: Any, *, given: str | None, applied: bool) -> str | None:
    """Which work item a write actually touched, or None if none did.

    `update` and `comment` are given the item. `create` is not -- the id exists
    only in the tracker's reply -- so the reply is read as well. Reading the
    argument alone would leave every create unrecorded.

    An unapplied write names nothing on purpose. Recording an intention is
    exactly what the declaration/action split exists to prevent: a declaration
    is a claim and an action is an observation, and `wrap` weighs one against
    the other.
    """
    if isinstance(result, StructuredError) or not applied:
        return None
    if isinstance(result, dict):
        item = result.get("item")
        if isinstance(item, dict) and item.get("ref"):
            return str(item["ref"])
    return given


def write_detail(result: Any) -> str:
    """A short human-readable trace of what the write did, for a wrap to print."""
    if not isinstance(result, dict):
        return ""
    changed = result.get("changed") or result.get("fields") or {}
    if isinstance(changed, dict) and changed:
        return ", ".join(f"{name}={value}" for name, value in sorted(changed.items()))
    return str(result.get("url") or "")


def record_tracker_write(
    store: SessionStore | None,
    session: str | None,
    *,
    verb: str,
    result: Any,
    given: str | None,
    applied: bool,
) -> TrackerWriteRecord:
    """Tell the session a tracker write really happened.

    Only when applied, and only once the write returned without a structured
    error: this is the observation `wrap` weighs against what an agent merely
    declared, so recording an intention would defeat the point of keeping them
    apart.

    Failing to record is never allowed to fail the write. The tracker has
    already changed by the time this runs, and a session bookkeeping problem
    must not be reported as though somebody's ticket did not update.

    Three things can go wrong, and all three are caught here rather than
    raised. :class:`SessionStateError` and ``ValueError`` say the session is
    not one this store can take an action for -- unopened, or an id that is
    not an id. :class:`SessionStoreError` says the store itself is unusable: a
    locked database, a full disk, a failed migration or a corrupt
    ``sessions.sqlite3``. None of these may fail a write the tracker has
    already accepted. Through MCP that would tell an agent its write had
    failed, and a retry of a create or a comment is a second item or a second
    comment.

    A caught failure is **returned, not swallowed**: the caller is expected to
    say that the write landed and the record did not.
    """
    item = written_item(result, given=given, applied=applied)
    if not session or item is None or store is None:
        return TrackerWriteRecord()
    try:
        action = SessionService(store).record_work_action(
            SessionId.parse(session),
            item=item,
            verb=verb,
            detail=write_detail(result),
        )
    except (SessionStateError, ValueError) as error:
        return TrackerWriteRecord(lost=f"session {session} could not take the record: {error}")
    except SessionStoreError as error:
        return TrackerWriteRecord(lost=str(error))
    return TrackerWriteRecord(action=action)


def noting_a_lost_record(result: Any, record: TrackerWriteRecord) -> Any:
    """The write's own payload, saying so when its record was lost.

    In one place because all three surfaces must say the same thing: `core/05`
    section 13 makes the CLI, the API and MCP thin clients over one service,
    and "the write landed, the session did not remember it" is a fact about
    the write rather than a fact about the interface that made it.

    Additive and absent when there is nothing to report, so the ordinary
    payload is the shape it has always been -- a reader that does not know the
    key sees no change, and one that does can tell that `wrap` will be short a
    record it should have had.
    """
    if record.lost is None or not isinstance(result, dict):
        return result
    return {**result, "record_lost": record.lost}
