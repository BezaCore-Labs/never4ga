"""ADR numbers, as a `Decisions/` folder carries them (core/02 section 21.11).

The number is part of the filename, `adr-[<series>-]NNNN_<slug>.md`, and is
scoped to the folder that holds it. A number typed by hand, or counted across
a family of workspaces rather than one folder, collides with an existing one.
So creation allocates, and `doctor` reports two records sharing one.

The filename is read rather than the title because it is the one place every
numbered folder agrees on: a record may be titled without its number. A number
in frontmatter would be a second copy of the same fact, and second copies go
stale.

A folder may run more than one series side by side -- `adr-infra-` and
`adr-app-`, say -- and each counts on its own. The unprefixed series is the
empty string. Gaps are legitimate.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

__all__ = ["DecisionNumber", "next_number", "number_in_filename", "number_in_title"]

#: Four digits at least, because that is what every numbered folder writes and
#: `adr-12_` is more likely a slug than a record.
_FILENAME: Final = re.compile(
    r"^adr-(?:(?P<series>[a-z][a-z0-9]*)-)?(?P<number>\d{4,})(?:_|\.md$)", re.IGNORECASE
)

#: A title *states* a number when it opens with one. `ADR 0001 — …` and
#: `ADR-0001 — …` both count.
_TITLE: Final = re.compile(
    r"^\s*adr[-\s]?(?:(?P<series>[a-z][a-z0-9]*)-)?(?P<number>\d+)\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True, order=True)
class DecisionNumber:
    """One record's place in its folder: a series, and a number within it."""

    series: str
    number: int

    @property
    def title_prefix(self) -> str:
        series = f"{self.series.upper()}-" if self.series else ""
        return f"ADR-{series}{self.number:04d}"


def number_in_filename(filename: str) -> DecisionNumber | None:
    """The number a decision's filename carries, or ``None`` if it carries none."""
    match = _FILENAME.match(filename)
    if match is None:
        return None
    return DecisionNumber((match["series"] or "").casefold(), int(match["number"]))


def number_in_title(title: str) -> DecisionNumber | None:
    """The number a title opens with, or ``None`` if it states none."""
    match = _TITLE.match(title)
    if match is None:
        return None
    return DecisionNumber((match["series"] or "").casefold(), int(match["number"]))


def next_number(existing: Iterable[DecisionNumber], series: str) -> DecisionNumber:
    """The next number in `series`: the highest present plus one, or 0001."""
    highest = max((one.number for one in existing if one.series == series), default=0)
    return DecisionNumber(series, highest + 1)
