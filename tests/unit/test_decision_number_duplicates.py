"""Two decisions in one folder sharing an ADR number (`core/02` sections 21.11 and 32).

Creation allocates the next number, but it cannot stop two sessions taking the
same one at the same moment, and a hand-written file never went through it.
So `doctor` reports a duplicate, on every record that carries it.

Only a duplicate. Gaps are legitimate, and so is the same number in two
folders: every workspace numbers its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.schema import Severity
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor, Finding

CODE = "decision_number_duplicate"
DECISIONS = "10_Workspaces/Acme/Decisions"
_IDS = count(1)


def fixed_clock() -> datetime:
    return datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def decision(vault: Path, filename: str, *, folder: str = DECISIONS) -> None:
    directory = vault / folder
    directory.mkdir(parents=True, exist_ok=True)
    concept_id = f"01a0e580-{next(_IDS):04x}-7000-8000-000000000001"
    title = filename.removesuffix(".md")
    (directory / filename).write_text(
        f"---\ntype: decision\nid: {concept_id}\nschema: never4ga/0.1\n"
        f'title: {title}\ncreated_at: "2026-09-01T12:00:00Z"\n'
        f"lifecycle: accepted\n---\n\n# {title}\n"
    )


def duplicates(vault: Path) -> list[Finding]:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
    ).diagnose()
    return [finding for finding in diagnosis.findings if finding.code == CODE]


class TestADuplicate:
    def test_is_reported_on_both_records(self, vault: Path) -> None:
        decision(vault, "adr-0033_one.md")
        decision(vault, "adr-0033_two.md")
        found = duplicates(vault)
        assert sorted(str(finding.path) for finding in found) == [
            f"{DECISIONS}/adr-0033_one.md",
            f"{DECISIONS}/adr-0033_two.md",
        ]

    def test_names_the_number_and_the_other_record(self, vault: Path) -> None:
        decision(vault, "adr-0033_one.md")
        decision(vault, "adr-0033_two.md")
        (first, _second) = sorted(duplicates(vault), key=lambda finding: str(finding.path))
        assert "ADR-0033" in first.message
        assert "adr-0033_two.md" in first.message

    def test_is_a_warning_with_a_repair_hint(self, vault: Path) -> None:
        decision(vault, "adr-0033_one.md")
        decision(vault, "adr-0033_two.md")
        for finding in duplicates(vault):
            assert finding.severity is Severity.WARNING
            assert finding.repair_hint is not None
            assert finding.document_id is not None

    def test_within_a_series(self, vault: Path) -> None:
        decision(vault, "adr-ops-0002_one.md")
        decision(vault, "adr-ops-0002_two.md")
        assert len(duplicates(vault)) == 2


class TestNotADuplicate:
    def test_a_gap(self, vault: Path) -> None:
        decision(vault, "adr-0011_one.md")
        decision(vault, "adr-0016_two.md")
        assert duplicates(vault) == []

    def test_the_same_number_in_two_series(self, vault: Path) -> None:
        decision(vault, "adr-ops-0001_one.md")
        decision(vault, "adr-web-0001_two.md")
        assert duplicates(vault) == []

    def test_the_same_number_in_two_folders(self, vault: Path) -> None:
        """A child workspace may hold a 0022 beside its parent's: numbers are per folder."""
        decision(vault, "adr-0022_parent.md")
        decision(vault, "adr-0022_child.md", folder="10_Workspaces/Acme/Workspaces/Desk/Decisions")
        assert duplicates(vault) == []

    def test_unnumbered_decisions(self, vault: Path) -> None:
        decision(vault, "one-thing.md")
        decision(vault, "another-thing.md")
        assert duplicates(vault) == []
