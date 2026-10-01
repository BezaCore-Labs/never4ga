"""The agent instruction files keep one contract and point at the facts' owners.

`AGENTS.md` is the one contract. It does not record which milestone is current
or what is deferred; it points at the owners instead: the maintainer
workspace's `Context/project-state.md`, and the `DEFERRED` tuple of
`test_milestone_boundary.py`. This guards against a pointer being deleted and the
fact written back in its place, where it would go stale.

`CLAUDE.md` is exactly an import of `AGENTS.md`, for Claude Code versions that do
not read `AGENTS.md` themselves. Anything more would be a second contract.

It is a repository test rather than a `doctor` rule because which pointers a
repository owes its readers is this repository's policy, not something the
product can impose on every mapped repository.

It cannot catch a stale copy written alongside an intact pointer. Detecting that
needs semantics, so only the pointer's presence is checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[2]

#: Each agent file, and the pointer strings it must keep.
#: Plain substrings rather than regexes: the pointer is an exact path or an
#: exact name, and a test that needs interpreting is a test that can drift.
POINTERS = {
    "AGENTS.md": (
        # The owner of "which milestone is current".
        "Context/project-state.md",
        # The owner of "what is deferred", and where it lives.
        "DEFERRED",
        "tests/architecture/test_milestone_boundary.py",
    ),
}


@pytest.mark.parametrize(("filename", "pointers"), POINTERS.items())
def test_the_agent_file_keeps_its_pointers(filename: str, pointers: tuple[str, ...]) -> None:
    text = (ROOT / filename).read_text(encoding="utf-8")
    missing = [pointer for pointer in pointers if pointer not in text]
    assert not missing, (
        f"{filename} no longer points at {missing}. The fact lives with "
        "its owner and this file points there. Restore the pointer; do not write "
        "the fact back here."
    )


def test_the_agent_files_exist() -> None:
    """A deleted file would otherwise pass the pointer test vacuously."""
    for filename in POINTERS:
        assert (ROOT / filename).is_file(), f"{filename} is required reading and is gone"


def test_claude_md_only_imports_the_contract() -> None:
    assert (ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
