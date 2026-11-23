"""`doctor` says when hand-written Markdown is waiting to be adopted.

A file with no frontmatter is legitimate prose, so the scan leaves it out of
every staleness comparison. A note written by hand in Obsidian is invisible to
`index`, `validate` and `search`, so `doctor` reports it: a *warning* that
names the file and the verb, because whether prose should become a concept is
the writer's call, not a repair.

The exclusions are the places where frontmatter-less Markdown is the design:
the Inbox (unprocessed by definition, with its own accounting), reserved
navigation, and the foreign-format islands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.schema import Severity
from never4ga.services import ContentService, VaultInitializer
from never4ga.services.doctor import Doctor, Finding
from never4ga.services.repository_pointers import (
    AGENT_FILENAMES,
    POINTER_MARKER,
    subordinate_body,
)


def fixed_clock() -> datetime:
    return datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def write(vault: Path, relative: str, text: str = "# By Hand\n\nprose\n") -> None:
    absolute = vault / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(text, encoding="utf-8")


def untracked_findings(vault: Path) -> dict[str, Finding]:
    diagnosis = Doctor(FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)).diagnose()
    return {
        str(finding.path): finding
        for finding in diagnosis.findings
        if finding.code == "untracked_document"
    }


class TestTheFindingNamesTheFile:
    def test_a_hand_written_note_is_reported(self, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/by-hand.md")

        assert "30_Knowledge/Notes/by-hand.md" in untracked_findings(vault)

    def test_it_is_a_warning_not_an_error(self, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/by-hand.md")

        finding = untracked_findings(vault)["30_Knowledge/Notes/by-hand.md"]
        assert finding.severity is Severity.WARNING

    def test_the_vault_stays_healthy_around_it(self, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/by-hand.md")

        diagnosis = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        assert diagnosis.healthy

    def test_the_hint_names_the_verb(self, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/by-hand.md")

        finding = untracked_findings(vault)["30_Knowledge/Notes/by-hand.md"]
        assert "never4ga adopt" in (finding.repair_hint or "")

    def test_adoption_clears_it(self, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/by-hand.md")
        documents = FileSystemMarkdownStore(vault)
        ContentService(FileSystemVaultFileStore(vault), documents, now=fixed_clock).adopt_concept(
            VaultPath.parse("30_Knowledge/Notes/by-hand.md")
        )

        assert untracked_findings(vault) == {}


class TestWhereProseIsTheDesign:
    def test_the_inbox_is_never_reported(self, vault: Path) -> None:
        write(vault, "00_Inbox/2026-09-07_thought.md", "a captured thought\n")

        assert untracked_findings(vault) == {}

    def test_reserved_navigation_is_never_reported(self, vault: Path) -> None:
        # index.md carries no concept frontmatter by rule, not by omission.
        assert untracked_findings(vault) == {}

    def test_foreign_format_islands_are_never_reported(self, vault: Path) -> None:
        write(vault, "50_System/Templates/essay.md", "# Essay\n")
        write(vault, "50_System/Skills/some-skill/notes.md", "payload\n")
        write(vault, "50_System/Integrations/OpenProject/setup.md", "tool notes\n")

        assert untracked_findings(vault) == {}

    def test_a_tracked_concept_is_not_reported(self, vault: Path) -> None:
        documents = FileSystemMarkdownStore(vault)
        ContentService(
            FileSystemVaultFileStore(vault), documents, now=fixed_clock
        ).create_knowledge("Tracked")

        assert untracked_findings(vault) == {}


class TestAGeneratedAgentPointer:
    """core/02 section 3.3: a pointer Never4gA generated is tool-owned.

    A vault that is itself a mapped repository holds the agent instruction
    pointers every mapped repository holds. They carry no frontmatter by
    design, and they identify themselves by their first line.
    """

    @pytest.mark.parametrize("name", AGENT_FILENAMES)
    def test_a_generated_pointer_at_the_root_is_not_reported(self, vault: Path, name: str) -> None:
        write(vault, name, subordinate_body(name))

        assert untracked_findings(vault) == {}

    def test_the_authoritative_pointer_is_not_reported(self, vault: Path) -> None:
        write(vault, "AGENTS.md", f"{POINTER_MARKER}\n\n# Agent instructions\n")

        assert untracked_findings(vault) == {}

    def test_a_generated_pointer_below_the_root_is_not_reported(self, vault: Path) -> None:
        write(vault, "10_Workspaces/Example/AGENTS.md", subordinate_body("AGENTS.md"))

        assert untracked_findings(vault) == {}

    def test_a_hand_written_agent_file_is_still_reported(self, vault: Path) -> None:
        write(vault, "AGENTS.md", "# How to work here\n\nprose\n")

        assert "AGENTS.md" in untracked_findings(vault)

    def test_the_marker_counts_only_on_the_first_line(self, vault: Path) -> None:
        write(vault, "CLAUDE.md", f"# Notes\n\n{POINTER_MARKER}\n")

        assert "CLAUDE.md" in untracked_findings(vault)

    def test_the_marker_does_not_excuse_another_name(self, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/copied.md", subordinate_body("AGENTS.md"))

        assert "30_Knowledge/Notes/copied.md" in untracked_findings(vault)
