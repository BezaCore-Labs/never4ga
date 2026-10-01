"""Capture: putting something in the Inbox without deciding what it is.

`core/01` section 5 makes `00_Inbox/` "temporary/unprocessed" and says it
"remains shallow". An inbox item is therefore deliberately **not a concept**: it
has no type, because choosing one is the act of processing it, and the Type
Registry binds no type to `00_Inbox/`. A plain Markdown file there is legal, and
`doctor` and `validate` correctly say nothing about it.

That is the whole reason this is a verb rather than an instruction to write a
file. There is no frontmatter to get right, but there is still a path to guess,
a name to collide and a timestamp to invent -- and `never4ga-capture` exists so
that a session can offload a thought in one call from any working directory.

The counterpart is `never4ga-create`, which is for when you *have* decided.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.errors import Never4gaError
from never4ga.layout import VaultRoot
from never4ga.ports.vault_files import VaultFileStore
from never4ga.services.authoring import Clock, format_timestamp, utc_now

__all__ = ["CaptureError", "CaptureService", "Captured"]

#: How many words of the note become its filename. Enough to recognise on
#: sight, short enough that a directory listing stays readable.
_TITLE_WORDS: Final = 8

_UNSAFE: Final = re.compile(r"[^\w\s-]")
_SPACES: Final = re.compile(r"[\s_]+")


class CaptureError(Never4gaError):
    """Nothing could be captured from what was given."""


@dataclass(frozen=True, slots=True)
class Captured:
    path: VaultPath
    title: str


class CaptureService:
    """Writes unprocessed notes into `00_Inbox/`."""

    def __init__(self, files: VaultFileStore, *, now: Clock = utc_now) -> None:
        self._files = files
        self._now = now

    def capture(self, text: str, *, title: str | None = None) -> Captured:
        """Write one note. The first line becomes the heading unless told otherwise.

        The filename is dated first so the Inbox sorts oldest-first without
        anything having to read the files. The `_` separates two different
        fields, the date and the subject.
        """
        body = text.strip()
        if not body:
            raise CaptureError("there is nothing to capture")

        heading = (title or body.splitlines()[0]).strip()
        stamp = format_timestamp(self._now())
        path = self._free_path(stamp[:10], heading)

        self._files.ensure_directory(VaultPath.parse(str(VaultRoot.INBOX)))
        self._files.write_text(path, f"# {heading}\n\nCaptured {stamp}.\n\n{body}\n")
        return Captured(path=path, title=heading)

    def _free_path(self, date: str, heading: str) -> VaultPath:
        """A path nothing is using. Capture never overwrites a previous thought."""
        stem = _slug(heading)
        for suffix in ("", *(f"-{n}" for n in range(2, 100))):
            path = VaultPath.parse(f"{VaultRoot.INBOX}/{date}_{stem}{suffix}.md")
            if not self._files.exists(path):
                return path
        raise CaptureError(f"{date}_{stem} is taken a hundred times over; name this one")


def _slug(heading: str) -> str:
    words = _SPACES.sub("-", _UNSAFE.sub("", heading).strip()).strip("-").casefold()
    trimmed = "-".join(part for part in words.split("-") if part)[:80]
    return "-".join(trimmed.split("-")[:_TITLE_WORDS]) or "note"
