"""Whether a pack item has passed the freshness instant it set for itself.

core/02 section 15.2: stale content remains available, is visibly flagged, and
is downgraded during context assembly. The rule itself -- ``now >=
stale_after`` -- lives in :mod:`never4ga.schema.freshness`, which `search` and
`doctor` already read; this only asks it on behalf of a pack, so the three
surfaces cannot disagree about which documents are stale.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from never4ga.domain.document import StoredDocument
from never4ga.schema.freshness import stale_instant

__all__ = ["Clock", "stale_since", "utc_now"]

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def stale_since(document: StoredDocument | None, now: datetime) -> datetime | None:
    """The `stale_after` `document` has passed by `now`, or None if it has not.

    None also when there is no document, no field, or a value the rule cannot
    read: section 15 treats absence as no claim, and a malformed instant is
    reported by validation rather than guessed at here.
    """
    if document is None:
        return None
    moment = stale_instant(document.frontmatter.get("stale_after"))
    return moment if moment is not None and moment <= now else None
