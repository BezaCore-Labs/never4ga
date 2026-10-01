"""`core/02` section 15: when a document has passed its own freshness window.

One rule, in one place, because three surfaces read it. `search` flags a result
as `is_stale`, `doctor` reports `content_is_stale` under section 32's
anti-drift list, and a Context Pack flags and downgrades a stale item under
section 15.2 (`never4ga.context.freshness`). A copy of this comparison in each
would be the same defect those checks exist to catch -- one drifting from the
others with nothing saying so.

The rule is section 15 as written:

    now >= stale_after

Note the boundary. At exactly `stale_after` the content *is* stale; an
implementation using ``moment < now`` disagrees with the specification for one
instant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["is_stale", "stale_instant"]


def stale_instant(value: Any) -> datetime | None:
    """The moment a document says it goes stale, or None if it does not say.

    None covers three cases that are all "no usable claim":

    - the field is absent, which section 15 defines as *no explicit staleness
      instant has been asserted* -- and explicitly not as content that can
      never go stale;
    - the value is not a string;
    - the value is a string this cannot read as an offset-bearing ISO 8601
      datetime, which section 7.4 requires of every timestamp field.

    That last case is left rather than guessed on purpose. A naive timestamp is
    malformed, not midnight in some assumed zone, and `schema/validation.py`
    already reports every one of them as `invalid_timestamp`. Inventing an
    offset here would have this rule assert something nobody wrote.
    """
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment


def is_stale(value: Any, now: datetime) -> bool:
    """Whether `value` is a staleness instant that `now` has reached."""
    moment = stale_instant(value)
    return moment is not None and moment <= now
