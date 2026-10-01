"""`doctor` inside foreign material (`core/01` section 1, `core/02` section 3.3).

Inside a directory `init` registered as foreign material there is no concept
to be broken. A note's frontmatter is the writer's, and the note is simply not
adopted yet. So a note without an `id` is `untracked_document`, a warning,
whether or not it has frontmatter of its own, and `init` over a pile of notes
leaves a healthy vault.

A top-level directory added by hand after `init` is neither a root nor
registered, and `doctor` says so rather than letting a later step index it
in silence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.schema import Severity
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor, Finding


def fixed_clock() -> datetime:
    return datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def write(root: Path, relative: str, text: str) -> None:
    absolute = root / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(text, encoding="utf-8")


WITH_FRONTMATTER = "---\ntags: [garden, soil]\nstatus: active\n---\n# Raised beds\n"
WITH_A_FOREIGN_ID = "---\nid: 42\ntitle: Kahneman\n---\nNotes on system one.\n"
PLAIN = "# Tuesday sync\n\nDecided to buy the drip irrigation kit.\n"


@pytest.fixture
def pile(tmp_path: Path) -> Path:
    """A pile, then `init` over it, so `Projects/` and `Reading/` are registered."""
    root = tmp_path / "vault"
    root.mkdir()
    write(root, "Projects/garden/raised-beds.md", WITH_FRONTMATTER)
    write(root, "Projects/garden/meetings/2026-05-12.md", PLAIN)
    write(root, "Reading/thinking-fast-and-slow.md", WITH_A_FOREIGN_ID)
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def findings(root: Path) -> list[Finding]:
    return list(
        Doctor(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).diagnose().findings
    )


def codes_at(root: Path, relative: str) -> list[str]:
    return [f.code for f in findings(root) if str(f.path) == relative]


class TestAForeignNoteIsUntrackedNotBroken:
    def test_frontmatter_of_its_own_is_not_a_missing_id(self, pile: Path) -> None:
        assert codes_at(pile, "Projects/garden/raised-beds.md") == ["untracked_document"]

    def test_a_foreign_id_that_is_not_ours_is_not_an_invalid_id(self, pile: Path) -> None:
        # `id: 42` is Jekyll's, or the writer's. It is not a broken UUIDv7.
        assert codes_at(pile, "Reading/thinking-fast-and-slow.md") == ["untracked_document"]

    def test_a_plain_note_is_untracked_as_before(self, pile: Path) -> None:
        assert codes_at(pile, "Projects/garden/meetings/2026-05-12.md") == ["untracked_document"]

    def test_every_one_of_them_is_a_warning(self, pile: Path) -> None:
        foreign = [f for f in findings(pile) if str(f.path).startswith(("Projects/", "Reading/"))]
        assert foreign and all(f.severity is Severity.WARNING for f in foreign)

    def test_the_vault_is_healthy_on_the_first_run(self, pile: Path) -> None:
        # No errors before anybody has made a single decision about their own
        # notes.
        assert [f for f in findings(pile) if f.severity is Severity.ERROR] == []

    def test_the_message_says_what_it_found(self, pile: Path) -> None:
        finding = next(f for f in findings(pile) if str(f.path) == "Projects/garden/raised-beds.md")
        assert "frontmatter of its own" in finding.message
        assert "adopt" in (finding.repair_hint or "")

    def test_outside_foreign_material_a_missing_id_is_still_an_error(self, pile: Path) -> None:
        write(pile, "30_Knowledge/Notes/lost.md", WITH_FRONTMATTER)
        assert codes_at(pile, "30_Knowledge/Notes/lost.md") == ["missing_id"]

    def test_a_directory_added_later_is_not_yet_foreign_material(self, pile: Path) -> None:
        # Until `init` registers it, its notes are judged as anywhere else's:
        # the exemption is the registration, never the location.
        write(pile, "Recipes/bread.md", WITH_FRONTMATTER)
        assert codes_at(pile, "Recipes/bread.md") == ["missing_id"]


class TestAnUnregisteredDirectoryIsReported:
    def test_a_top_level_directory_added_after_init_is_reported(self, pile: Path) -> None:
        write(pile, "Recipes/bread.md", PLAIN)
        found = [f for f in findings(pile) if f.code == "foreign_material_unregistered"]
        assert [str(f.path) for f in found] == ["Recipes"]
        assert found[0].severity is Severity.WARNING
        assert "never4ga init" in (found[0].repair_hint or "")

    def test_registered_directories_and_roots_are_not(self, pile: Path) -> None:
        assert [f for f in findings(pile) if f.code == "foreign_material_unregistered"] == []

    def test_a_fresh_vault_has_nothing_to_say(self, tmp_path: Path) -> None:
        root = tmp_path / "fresh"
        root.mkdir()
        VaultInitializer(
            FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
        ).initialize("Fresh")
        assert [f for f in findings(root) if f.code == "foreign_material_unregistered"] == []

    def test_the_next_init_registers_it_and_the_finding_clears(self, pile: Path) -> None:
        write(pile, "Recipes/bread.md", PLAIN)
        FileSystemMarkdownStore(pile).refresh()
        VaultInitializer(
            FileSystemVaultFileStore(pile), FileSystemMarkdownStore(pile), now=fixed_clock
        ).initialize("Test Vault")
        assert [f for f in findings(pile) if f.code == "foreign_material_unregistered"] == []
        assert codes_at(pile, "Recipes/bread.md") == ["untracked_document"]
