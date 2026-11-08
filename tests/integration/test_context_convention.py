"""Every live workspace carries an AI Context, and `doctor` says when one does not.

core/03 section 5.1: a workspace's lasting rules live in
`Context/agent-context.md`, which startup delivers as required reading
(core/07 section 10). core/03 section 2: creating a workspace writes it.
core/02 section 32: `doctor` reports a live workspace without one, and a
relative link to a folder that does not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.schema import Severity, ValidationLevel, validate_document
from never4ga.services import ContentService, Doctor, VaultInitializer
from never4ga.services.doctor import Finding


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


@pytest.fixture
def content(vault: Path) -> ContentService:
    documents = FileSystemMarkdownStore(vault)
    documents.refresh()
    return ContentService(FileSystemVaultFileStore(vault), documents, now=fixed_clock)


def findings(vault: Path, code: str) -> list[Finding]:
    diagnosis = Doctor(FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)).diagnose()
    return [finding for finding in diagnosis.findings if finding.code == code]


def write(vault: Path, relative: str, text: str) -> None:
    absolute = vault / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(text, encoding="utf-8")


def hand_written_workspace(
    vault: Path, directory: str, *, lifecycle: str = "active", body: str = ""
) -> None:
    write(
        vault,
        f"{directory}/workspace.md",
        "---\n"
        "type: workspace\n"
        "id: 01a1b9c0-0000-7000-8000-000000000001\n"
        "schema: never4ga/0.1\n"
        "title: Harbour Survey\n"
        'created_at: "2026-09-07T12:00:00Z"\n'
        "workspace_type: project\n"
        f"lifecycle: {lifecycle}\n"
        "---\n"
        f"# Harbour Survey\n\n{body}",
    )


class TestCreationWritesTheAiContext:
    def test_a_new_workspace_has_one(self, content: ContentService, vault: Path) -> None:
        content.create_workspace("Harbour Survey", workspace_type="project")
        assert (vault / "10_Workspaces/Harbour-Survey/Context/agent-context.md").is_file()

    def test_it_is_a_context_document_of_that_workspace(
        self, content: ContentService, vault: Path
    ) -> None:
        created = content.create_workspace("Harbour Survey", workspace_type="project")
        documents = FileSystemMarkdownStore(vault)
        documents.refresh()
        context = documents.get_by_path(
            VaultPath.parse("10_Workspaces/Harbour-Survey/Context/agent-context.md")
        )
        assert context is not None
        assert context.frontmatter["type"] == "context"
        assert context.frontmatter["title"] == "AI Context"
        assert context.frontmatter["workspace"] == str(created.concept_id)

    def test_it_validates_strictly(self, content: ContentService, vault: Path) -> None:
        content.create_workspace("Harbour Survey", workspace_type="project")
        documents = FileSystemMarkdownStore(vault)
        documents.refresh()
        path = VaultPath.parse("10_Workspaces/Harbour-Survey/Context/agent-context.md")
        context = documents.get_by_path(path)
        assert context is not None
        report = validate_document(path, context.frontmatter, level=ValidationLevel.STRICT)
        assert report.ok, report.issues

    def test_it_carries_the_sections_an_agent_needs(
        self, content: ContentService, vault: Path
    ) -> None:
        content.create_workspace("Harbour Survey", workspace_type="project")
        text = (vault / "10_Workspaces/Harbour-Survey/Context/agent-context.md").read_text(
            encoding="utf-8"
        )
        for heading in (
            "## In one paragraph",
            "## Where authority lives",
            "## What an agent may change here",
            "## Checks",
            "## Traps",
            "## Conventions",
        ):
            assert heading in text

    def test_a_child_workspace_gets_its_own(self, content: ContentService, vault: Path) -> None:
        parent = content.create_workspace("Harbour Survey", workspace_type="project")
        content.create_workspace("Tide Gauges", workspace_type="project", parent=parent.concept_id)
        assert (
            vault / "10_Workspaces/Harbour-Survey/Workspaces/Tide-Gauges/Context/agent-context.md"
        ).is_file()

    def test_the_result_names_what_was_written(self, content: ContentService) -> None:
        created = content.create_workspace("Harbour Survey", workspace_type="project")
        assert VaultPath.parse("10_Workspaces/Harbour-Survey/Context/agent-context.md") in (
            created.files
        )

    def test_the_new_workspace_raises_no_finding(
        self, content: ContentService, vault: Path
    ) -> None:
        content.create_workspace("Harbour Survey", workspace_type="project")
        diagnosis = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        assert diagnosis.healthy, diagnosis.errors
        assert diagnosis.warnings == ()


class TestTheKeyLinks:
    def test_the_page_links_the_ai_context_file(self, content: ContentService, vault: Path) -> None:
        content.create_workspace("Harbour Survey", workspace_type="project")
        page = (vault / "10_Workspaces/Harbour-Survey/workspace.md").read_text(encoding="utf-8")
        assert "[AI Context](Context/agent-context.md)" in page
        assert "(Context/)" not in page
        assert "(Logs/)" not in page


class TestAMissingAiContextIsReported:
    def test_a_live_workspace_without_one_is_a_warning(self, vault: Path) -> None:
        hand_written_workspace(vault, "10_Workspaces/Harbour-Survey")
        [finding] = findings(vault, "agent_context_missing")
        assert finding.severity is Severity.WARNING
        assert str(finding.path) == "10_Workspaces/Harbour-Survey/workspace.md"
        assert "Harbour Survey" in finding.message

    def test_the_hint_says_where_it_goes(self, vault: Path) -> None:
        hand_written_workspace(vault, "10_Workspaces/Harbour-Survey")
        [finding] = findings(vault, "agent_context_missing")
        assert finding.repair_hint is not None
        assert "Context/agent-context.md" in finding.repair_hint

    @pytest.mark.parametrize("lifecycle", ["completed", "cancelled", "retired"])
    def test_a_finished_workspace_is_not_asked_for_one(self, vault: Path, lifecycle: str) -> None:
        hand_written_workspace(vault, "10_Workspaces/Harbour-Survey", lifecycle=lifecycle)
        assert findings(vault, "agent_context_missing") == []

    def test_an_archived_workspace_is_not_asked_for_one(self, vault: Path) -> None:
        hand_written_workspace(vault, "90_Archive/Workspaces/Harbour-Survey")
        assert findings(vault, "agent_context_missing") == []

    def test_a_created_workspace_is_not_reported(
        self, content: ContentService, vault: Path
    ) -> None:
        content.create_workspace("Harbour Survey", workspace_type="project")
        assert findings(vault, "agent_context_missing") == []


class TestFolderLinksAreChecked:
    def test_a_link_to_a_missing_folder_is_broken(self, vault: Path) -> None:
        hand_written_workspace(
            vault, "10_Workspaces/Harbour-Survey", body="- [Soundings](Soundings/)\n"
        )
        broken = findings(vault, "broken_link")
        assert [str(finding.path) for finding in broken] == [
            "10_Workspaces/Harbour-Survey/workspace.md"
        ]
        assert "Soundings" in broken[0].message
        assert broken[0].severity is Severity.WARNING

    def test_a_link_to_a_folder_that_exists_is_not(self, vault: Path) -> None:
        hand_written_workspace(
            vault, "10_Workspaces/Harbour-Survey", body="- [Soundings](Soundings/)\n"
        )
        (vault / "10_Workspaces/Harbour-Survey/Soundings").mkdir()
        assert findings(vault, "broken_link") == []

    def test_a_folder_link_is_read_relative_to_its_document(self, vault: Path) -> None:
        hand_written_workspace(
            vault, "10_Workspaces/Harbour-Survey", body="- [Notes](../../30_Knowledge/Notes/)\n"
        )
        assert findings(vault, "broken_link") == []

    def test_a_folder_link_in_code_is_not_a_link(self, vault: Path) -> None:
        hand_written_workspace(
            vault, "10_Workspaces/Harbour-Survey", body="Write `[Soundings](Soundings/)`.\n"
        )
        assert findings(vault, "broken_link") == []

    def test_an_external_folder_link_is_not_checked(self, vault: Path) -> None:
        hand_written_workspace(
            vault, "10_Workspaces/Harbour-Survey", body="[Site](https://example.org/docs/)\n"
        )
        assert findings(vault, "broken_link") == []
