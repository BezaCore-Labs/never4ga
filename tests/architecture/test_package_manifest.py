"""`PACKAGE_MANIFEST.md` is a tamper-evidence mechanism, so verify it.

The manifest records a SHA-256 and byte size for every packaged document. That
makes an unrecorded edit detectable, but only if something checks.

This matters beyond bookkeeping. `AGENTS.md` is the binding agent contract, and
agents edit files. Nothing can stop a tool from writing to it, but the build can
refuse to pass afterwards. Detection is tool-agnostic in a way that per-tool
prevention is not: it does not care which client made the change, or whether
that client honours permission rules at all.

Editing a packaged document is fine. Editing one *without recording it* is not.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Final, NamedTuple

import pytest

pytestmark = pytest.mark.architecture

ROOT: Final = Path(__file__).resolve().parents[2]
MANIFEST: Final = ROOT / "PACKAGE_MANIFEST.md"

_ROW: Final = re.compile(
    r"^\| `(?P<path>[^`]+)` \| `(?P<digest>[0-9a-f]{64})` \| (?P<size>\d+) \|$"
)

#: Directories whose every Markdown document must appear in the manifest, so a
#: new specification cannot arrive unrecorded.
TRACKED_TREES: Final = ("bootstrap",)

#: Root-level documents that must always be recorded. `LICENSE` is among them
#: because the licence grant is the one document no change may slip past.
TRACKED_ROOT_FILES: Final = (
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "README.md",
)


class Entry(NamedTuple):
    path: str
    digest: str
    size: int


def manifest_entries() -> list[Entry]:
    rows = [_ROW.match(line) for line in MANIFEST.read_text().splitlines()]
    return [Entry(m.group("path"), m.group("digest"), int(m.group("size"))) for m in rows if m]


ENTRIES: Final = manifest_entries()


class TestManifestIntegrity:
    def test_the_manifest_lists_documents(self) -> None:
        assert len(ENTRIES) >= 6

    @pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.path)
    def test_document_matches_its_recorded_hash(self, entry: Entry) -> None:
        path = ROOT / entry.path
        assert path.is_file(), f"{entry.path} is in the manifest but not on disk"
        data = path.read_bytes()
        assert len(data) == entry.size, (
            f"{entry.path} is {len(data)} bytes, manifest says {entry.size}. "
            "If the edit was intended, update PACKAGE_MANIFEST.md in the same change."
        )
        assert hashlib.sha256(data).hexdigest() == entry.digest, (
            f"{entry.path} does not match its recorded hash. If the edit was "
            "intended, update PACKAGE_MANIFEST.md in the same change; if it was "
            "not, find out what wrote to it."
        )


class TestManifestCompleteness:
    """A document that is not listed cannot be tamper-evident."""

    @pytest.mark.parametrize("name", TRACKED_ROOT_FILES)
    def test_root_document_is_recorded(self, name: str) -> None:
        if not (ROOT / name).exists():
            pytest.skip(f"{name} is not present in this repository")
        assert name in {entry.path for entry in ENTRIES}

    @pytest.mark.parametrize("tree", TRACKED_TREES)
    def test_every_document_in_the_tree_is_recorded(self, tree: str) -> None:
        recorded = {entry.path for entry in ENTRIES}
        present = {
            str(path.relative_to(ROOT))
            for path in (ROOT / tree).rglob("*.md")
            if ".git" not in path.parts
        }
        assert present <= recorded, (
            f"unrecorded documents under {tree}/: {sorted(present - recorded)}. "
            "Add them to PACKAGE_MANIFEST.md."
        )

    def test_no_entry_points_at_a_missing_file(self) -> None:
        missing = [entry.path for entry in ENTRIES if not (ROOT / entry.path).exists()]
        assert missing == []


class TestTheContractIsCovered:
    """`AGENTS.md` is the binding contract; its integrity is the point."""

    def test_the_agent_contract_is_recorded(self) -> None:
        assert "AGENTS.md" in {entry.path for entry in ENTRIES}

    def test_no_specification_has_crept_back_into_the_repository(self) -> None:
        # The specifications are maintained outside this repository and
        # exported to docs/specs/. The manifest guards the agent contract and
        # the bootstrap skills.
        assert not (ROOT / "specification").exists()
        assert not [e.path for e in ENTRIES if e.path.startswith("specification/")]

    def test_no_decision_record_has_crept_back_into_the_repository(self) -> None:
        # Decision records are kept outside the repository, which holds the
        # product. One appearing here would be a second source for the same
        # decisions.
        assert not (ROOT / "decisions").exists()
        assert not [e.path for e in ENTRIES if e.path.startswith("decisions/")]
