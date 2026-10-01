"""Two checks on text a model is going to read.

Both exist because vendored material is *text that instructs*. A Skill or an
agent definition is read by a model and acted on, so the failure modes are not
a corrupt file or a crash -- they are a file that does what it appears to do
plus something else, and a file that works here and nowhere else.

**Invisible characters.** The Unicode Tag block `U+E0000-E007F` was proposed
for language tagging in Unicode 3.1 and deprecated in 5.1; no legitimate text
uses it. It is the canonical vector for hiding instructions inside
ASCII-looking strings: the tag bytes carry a readable sentence, the model
consumes them, and the human reviewing the diff sees nothing at all. Bidi
overrides are the Trojan Source class and zero-width characters are the same
idea with worse ergonomics.

**Personal absolute paths.** A vendored artifact with somebody's home directory
baked into it works on one machine and nowhere else. The rule captures the
*username* and allows a placeholder vocabulary, so `/home/username/...` in a
template passes and `/home/alice/...` does not. One rule, no per-file
suppressions, and the placeholder list doubles as documentation of what an
author should have written.

Pure functions over text. Walking a directory belongs to a file store and
deciding what to do with a finding belongs to a service.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

__all__ = [
    "PLACEHOLDER_USERNAMES",
    "TextHazard",
    "find_invisible_characters",
    "find_personal_paths",
    "scan_text",
]


def _tag_block() -> dict[int, str]:
    return dict.fromkeys(range(0xE0000, 0xE0080), "tag_character")


#: Every code point worth refusing outright, and what to call it. Written in
#: hex deliberately: `range(0xE0000, 0xE0080)` is recognisably the Unicode tag
#: block to anyone who has met it, and the decimal a formatter prefers is not.
#: Named rather than expressed as ranges alone so a finding can say which
#: family it belongs to; "U+200B" tells a reader nothing on its own.
INVISIBLE: Final[dict[int, str]] = {
    **dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF), "zero_width"),
    **dict.fromkeys((*range(0x202A, 0x202F), *range(0x2066, 0x206A)), "bidi_override"),
    **dict.fromkeys((*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)), "variation_selector"),
    **_tag_block(),
    **dict.fromkeys(range(0x2061, 0x2065), "invisible_operator"),
    0x180E: "format_control",
    **dict.fromkeys((0x115F, 0x1160, 0x3164), "hangul_filler"),
}

#: Usernames that are obviously stand-ins. The list is the vocabulary an author
#: is expected to use, which is why it is short and why extending it is a
#: deliberate act rather than a way to silence a finding.
PLACEHOLDER_USERNAMES: Final = frozenset(
    {
        "user",
        "username",
        "youruser",
        "yourusername",
        "your-username",
        "example",
        "me",
        "you",
        "name",
    }
)

_HOME_PATTERNS: Final = (
    ("posix", re.compile(r"/home/([A-Za-z][A-Za-z0-9._-]*)")),
    ("macos", re.compile(r"/Users/([A-Za-z][A-Za-z0-9._-]*)")),
    ("windows", re.compile(r"[Cc]:\\Users\\([A-Za-z][A-Za-z0-9._-]*)")),
)


@dataclass(frozen=True, slots=True)
class TextHazard:
    """One reason a piece of text should not be vendored as it stands."""

    kind: str
    line: int
    column: int
    detail: str

    def __str__(self) -> str:
        return f"{self.line}:{self.column} {self.kind}: {self.detail}"


def find_invisible_characters(text: str) -> Iterator[TextHazard]:
    """Every code point a reviewer cannot see.

    Reports position, always. An invisible character cannot be found by reading
    the file, so a finding that does not say where it is leaves the reader no
    better off than no finding at all.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        for column, character in enumerate(line, start=1):
            kind = INVISIBLE.get(ord(character))
            if kind is None:
                continue
            name = unicodedata.name(character, "unnamed")
            yield TextHazard(kind, number, column, f"U+{ord(character):04X} {name}")


def find_personal_paths(
    text: str, *, forbidden: frozenset[str] = frozenset()
) -> Iterator[TextHazard]:
    """Home directories naming a real person, and any path outright banned.

    ``forbidden`` carries literals that must never appear whatever they look
    like -- the vault's own absolute path, say, which is not a username match
    and would otherwise sail through.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        for label, pattern in _HOME_PATTERNS:
            for match in pattern.finditer(line):
                if match.group(1).casefold() in PLACEHOLDER_USERNAMES:
                    continue
                yield TextHazard(
                    f"personal_path_{label}",
                    number,
                    match.start() + 1,
                    f"{match.group(0)} names a real user; use a placeholder",
                )
        for literal in forbidden:
            index = line.find(literal)
            if index >= 0:
                yield TextHazard(
                    "forbidden_path",
                    number,
                    index + 1,
                    f"{literal} is a path this machine must not bake into vendored text",
                )


def scan_text(text: str, *, forbidden: frozenset[str] = frozenset()) -> tuple[TextHazard, ...]:
    """Both checks, in reading order."""
    found = [*find_invisible_characters(text), *find_personal_paths(text, forbidden=forbidden)]
    return tuple(sorted(found, key=lambda hazard: (hazard.line, hazard.column)))
