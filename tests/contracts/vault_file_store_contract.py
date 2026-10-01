"""VaultFileStore contract."""

from __future__ import annotations

import pytest

from never4ga.domain.document import VaultPath
from never4ga.ports.vault_files import VaultFileStore


def p(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


class VaultFileStoreContract:
    """Behaviour every VaultFileStore implementation must exhibit."""

    @pytest.fixture
    def files(self) -> VaultFileStore:
        raise NotImplementedError("supply a VaultFileStore fixture")

    def test_a_written_file_can_be_read_back(self, files: VaultFileStore) -> None:
        files.write_text(p("30_Knowledge/index.md"), "# Knowledge\n")
        assert files.read_text(p("30_Knowledge/index.md")) == "# Knowledge\n"

    def test_reading_a_missing_file_returns_none(self, files: VaultFileStore) -> None:
        assert files.read_text(p("nope.md")) is None

    def test_writing_creates_parent_directories(self, files: VaultFileStore) -> None:
        files.write_text(p("10_Workspaces/A/Workspaces/B/index.md"), "x")
        assert files.is_directory(p("10_Workspaces/A/Workspaces/B"))

    def test_writing_replaces_existing_content(self, files: VaultFileStore) -> None:
        files.write_text(p("a.md"), "first")
        files.write_text(p("a.md"), "second")
        assert files.read_text(p("a.md")) == "second"

    def test_exists_reports_files_and_directories(self, files: VaultFileStore) -> None:
        files.ensure_directory(p("00_Inbox"))
        files.write_text(p("home.md"), "# Home\n")
        assert files.exists(p("00_Inbox"))
        assert files.exists(p("home.md"))
        assert not files.exists(p("90_Archive"))

    def test_is_directory_distinguishes_the_two(self, files: VaultFileStore) -> None:
        files.ensure_directory(p("00_Inbox"))
        files.write_text(p("home.md"), "# Home\n")
        assert files.is_directory(p("00_Inbox"))
        assert not files.is_directory(p("home.md"))

    def test_ensure_directory_is_idempotent(self, files: VaultFileStore) -> None:
        files.ensure_directory(p("30_Knowledge/Notes"))
        files.ensure_directory(p("30_Knowledge/Notes"))
        assert files.is_directory(p("30_Knowledge/Notes"))

    def test_ensure_directory_creates_parents(self, files: VaultFileStore) -> None:
        files.ensure_directory(p("50_System/Integrations/Obsidian/Bases"))
        assert files.is_directory(p("50_System/Integrations/Obsidian"))
        assert files.is_directory(p("50_System"))

    def test_ensure_directory_does_not_disturb_existing_content(
        self, files: VaultFileStore
    ) -> None:
        files.write_text(p("30_Knowledge/Notes/a.md"), "keep")
        files.ensure_directory(p("30_Knowledge/Notes"))
        assert files.read_text(p("30_Knowledge/Notes/a.md")) == "keep"

    def test_iter_paths_yields_every_file(self, files: VaultFileStore) -> None:
        files.write_text(p("home.md"), "a")
        files.write_text(p("30_Knowledge/Notes/b.md"), "b")
        files.ensure_directory(p("00_Inbox"))
        assert {str(path) for path in files.iter_paths()} == {
            "home.md",
            "30_Knowledge/Notes/b.md",
        }

    def test_iter_paths_is_empty_for_an_empty_vault(self, files: VaultFileStore) -> None:
        assert list(files.iter_paths()) == []

    def test_iter_directories_yields_every_directory(self, files: VaultFileStore) -> None:
        files.ensure_directory(p("30_Knowledge/Notes"))
        files.write_text(p("50_System/system.md"), "x")
        assert {str(path) for path in files.iter_directories()} == {
            "30_Knowledge",
            "30_Knowledge/Notes",
            "50_System",
        }

    def test_non_markdown_files_are_carried_too(self, files: VaultFileStore) -> None:
        # Obsidian .base views are vault content Never4gA writes but never
        # treats as a concept (core/02 section 3.3).
        files.write_text(p("50_System/Integrations/Obsidian/Bases/all.base"), "{}")
        assert files.read_text(p("50_System/Integrations/Obsidian/Bases/all.base")) == "{}"
        assert str(p("50_System/Integrations/Obsidian/Bases/all.base")) in {
            str(path) for path in files.iter_paths()
        }

    def test_unicode_survives_a_round_trip(self, files: VaultFileStore) -> None:
        files.write_text(p("30_Knowledge/Notes/Ålesund.md"), "Fjørd — naïve\n")
        assert files.read_text(p("30_Knowledge/Notes/Ålesund.md")) == "Fjørd — naïve\n"
