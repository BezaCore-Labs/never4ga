"""The Markdown Never4gA ships, and this repository's own, is one line per paragraph.

A viewer that shows every newline as a line break, as Obsidian does by default,
turns prose broken at a fixed width into half-width, ragged paragraphs. So a
paragraph, list item or blockquote is written as one line and the viewer wraps
it. These tests find a line that continues the line above it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from never4ga.services.scaffold import (
    DOMAIN_REGISTRY_BODY,
    HOME_BODY,
    INBOX_SCRATCHPAD,
    OBSIDIAN_README,
    ROOT_INDEX,
    ROOT_INDEXES,
    SHIPPED_TEMPLATES,
    SKILLS,
    SYSTEM_MANIFEST_BODY,
)

ROOT: Final = Path(__file__).resolve().parents[2]

#: A line that opens a block of its own, so it never continues the line above.
_OPENS_A_BLOCK: Final = re.compile(
    r"^\s*([-*+]\s|\d+[.)]\s|#{1,6}(\s|$)|\||```|~~~|<|\[[^\]]+\]:|\[!|---\s*$)"
)
_FENCE: Final = re.compile(r"^\s*(```|~~~)")
_QUOTE: Final = re.compile(r"^\s*((?:>\s?)*)")


def continuation_lines(text: str) -> list[str]:
    """Every line that carries on the paragraph, item or quote above it."""
    lines = text.splitlines()
    found: list[str] = []
    in_fence = False
    start = 0
    if lines and lines[0] == "---":
        start = lines.index("---", 1) + 1
    previous_prose_depth: int | None = None
    for line in lines[start:]:
        if _FENCE.match(line):
            in_fence = not in_fence
            previous_prose_depth = None
            continue
        quote = _QUOTE.match(line)
        assert quote is not None  # the quote prefix is optional, so every line matches
        depth = quote.group(1).count(">")
        content = line[quote.end() :] if depth else line
        if in_fence or not content.strip():
            previous_prose_depth = None
            continue
        if previous_prose_depth == depth and not _OPENS_A_BLOCK.match(content):
            found.append(line)
        is_prose = not content.lstrip().startswith(("#", "|", "<", "[!"))
        is_prose = is_prose and not line.endswith(("  ", "\\"))
        previous_prose_depth = depth if is_prose else None
    return found


def _shipped() -> dict[str, str]:
    documents = {
        "ROOT_INDEX": ROOT_INDEX,
        "HOME_BODY": HOME_BODY,
        "DOMAIN_REGISTRY_BODY": DOMAIN_REGISTRY_BODY,
        "SYSTEM_MANIFEST_BODY": SYSTEM_MANIFEST_BODY,
        "INBOX_SCRATCHPAD": INBOX_SCRATCHPAD,
        "OBSIDIAN_README": OBSIDIAN_README,
    }
    documents.update({f"index {name}": text for name, text in ROOT_INDEXES.items()})
    documents.update({f"template {name}": text for name, text in SHIPPED_TEMPLATES.items()})
    documents.update({f"skill {name}": text for name, text in SKILLS.items()})
    return documents


def _repository_documents() -> list[Path]:
    paths = [ROOT / name for name in ("README.md", "AGENTS.md", "SECURITY.md")]
    return paths + sorted((ROOT / "bootstrap").rglob("*.md"))


def test_a_wrapped_paragraph_is_found() -> None:
    text = (
        "# Title\n\nOne sentence that was broken\nat a fixed width.\n\n- an item\n  carried on\n\n"
        "> a quote\n> carried on\n"
    )
    assert continuation_lines(text) == ["at a fixed width.", "  carried on", "> carried on"]


def test_code_tables_headings_and_hard_breaks_are_not_prose() -> None:
    text = (
        "---\nname: x\ndescription: y\n---\n# Heading\n```bash\none\ntwo\n```\n"
        "| a |\n| - |\nline one  \nline two\n> [!note]\n> body\n"
    )
    assert continuation_lines(text) == []


@pytest.mark.parametrize(("name", "text"), sorted(_shipped().items()))
def test_shipped_markdown_is_one_line_per_paragraph(name: str, text: str) -> None:
    assert continuation_lines(text) == [], name


@pytest.mark.parametrize("path", _repository_documents(), ids=lambda path: path.name)
def test_repository_markdown_is_one_line_per_paragraph(path: Path) -> None:
    assert continuation_lines(path.read_text()) == [], path
