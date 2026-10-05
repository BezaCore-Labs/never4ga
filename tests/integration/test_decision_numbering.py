"""Creating a decision allocates its ADR number (core/02 section 21.11).

The number is the next one *in the folder the decision lands in*, not the
highest across the workspace family, and a title stating any other is refused.

Existing records are written straight to disk, the way hand-numbered folders
are.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.identity import ConceptId
from never4ga.services import ConceptCreationError, ContentService, VaultInitializer

DECISIONS = "10_Workspaces/BezaCore/Decisions"


def fixed_clock() -> datetime:
    return datetime(2026, 9, 27, 19, 0, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def content(root: Path) -> ContentService:
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents, now=fixed_clock).initialize("Test Vault")
    documents.refresh()
    return ContentService(files, documents, now=fixed_clock)


@pytest.fixture
def workspace(content: ContentService) -> ConceptId:
    return content.create_workspace("BezaCore", workspace_type="organization").concept_id


def existing(root: Path, *filenames: str, folder: str = DECISIONS) -> None:
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        (directory / filename).write_text("# hand-written\n", encoding="utf-8")


class TestAFolderThatNumbers:
    def test_the_next_number_is_allocated_into_title_and_filename(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0001_first.md", "adr-0002_second.md")
        created = content.create_concept("decision", "Use SQLite First", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0003 — Use SQLite First"
        assert str(created.path) == f"{DECISIONS}/adr-0003_use-sqlite-first.md"

    def test_the_report_says_which_number_and_why(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0001_first.md")
        created = content.create_concept("decision", "Use SQLite First", workspace=workspace)
        assert "ADR-0002" in created.placement_reason

    def test_a_gap_is_kept_and_the_highest_wins(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0011_a.md", "adr-0012_b.md", "adr-0016_c.md")
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0017 — Next"

    def test_unnumbered_records_beside_numbered_ones_do_not_count(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0004_a.md", "phase-a-f-design-decisions.md")
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0005 — Next"

    def test_another_folder_s_numbers_are_not_this_folder_s(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        """The workspace family's highest number is not the next one here."""
        existing(root, "adr-0011_a.md")
        existing(
            root,
            "adr-0032_elsewhere.md",
            folder="10_Workspaces/BezaCore/Workspaces/Never4gA/Decisions",
        )
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0012 — Next"


ARCHIVED = "90_Archive/Workspaces/BezaCore/Decisions"


class TestAnArchivedRecordKeepsItsNumber:
    """Archiving moves a record out of its folder. Its number is still taken."""

    def test_the_highest_record_archived_is_not_reissued(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0001_first.md", "adr-0002_second.md")
        existing(root, "adr-0003_withdrawn.md", folder=ARCHIVED)
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0004 — Next"

    def test_a_lower_archived_record_changes_nothing(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0005_live.md")
        existing(root, "adr-0002_withdrawn.md", folder=ARCHIVED)
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0006 — Next"

    def test_stating_an_archived_record_s_number_is_refused(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0001_first.md")
        existing(root, "adr-0002_withdrawn.md", folder=ARCHIVED)
        with pytest.raises(ConceptCreationError, match="ADR-0003"):
            content.create_concept("decision", "ADR-0002 — Again", workspace=workspace)

    def test_a_folder_whose_every_record_was_archived_goes_on_counting(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0001_withdrawn.md", "adr-0002_withdrawn.md", folder=ARCHIVED)
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0003 — Next"

    def test_series_are_counted_apart_in_the_archive_too(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-infra-0001_a.md", "adr-app-0004_b.md")
        existing(root, "adr-infra-0002_withdrawn.md", folder=ARCHIVED)
        created = content.create_concept("decision", "Next", workspace=workspace, series="infra")
        assert created.document.frontmatter["title"] == "ADR-INFRA-0003 — Next"

    def test_another_workspace_s_archive_is_not_this_folder_s(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0001_first.md")
        existing(
            root,
            "adr-0016_elsewhere.md",
            folder="90_Archive/Workspaces/BezaCore/Workspaces/Marketing/Decisions",
        )
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0002 — Next"

    def test_the_report_says_the_archive_was_counted(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0001_first.md")
        existing(root, "adr-0002_withdrawn.md", folder=ARCHIVED)
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert ARCHIVED in created.placement_reason


class TestAStatedNumber:
    def test_the_allocated_number_stated_by_hand_is_kept_as_written(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0011_a.md")
        created = content.create_concept("decision", "ADR-0012 — Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-0012 — Next"
        assert str(created.path) == f"{DECISIONS}/adr-0012_next.md"

    def test_any_other_number_is_refused_and_the_next_is_named(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0011_a.md")
        with pytest.raises(ConceptCreationError, match="ADR-0012") as refused:
            content.create_concept("decision", "ADR-0033 — Next", workspace=workspace)
        assert "ADR-0033" in str(refused.value)
        assert not (root / DECISIONS / "adr-0033_next.md").exists()

    def test_a_number_already_taken_is_refused(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0011_a.md")
        with pytest.raises(ConceptCreationError, match="ADR-0012"):
            content.create_concept("decision", "ADR-0011 — Again", workspace=workspace)


class TestAFolderThatDoesNotNumber:
    def test_it_stays_unnumbered(self, content: ContentService, workspace: ConceptId) -> None:
        created = content.create_concept("decision", "Use SQLite First", workspace=workspace)
        assert created.document.frontmatter["title"] == "Use SQLite First"
        assert str(created.path) == f"{DECISIONS}/use-sqlite-first.md"

    def test_asking_for_a_number_starts_at_one(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "decision", "Use SQLite First", workspace=workspace, numbered=True
        )
        assert created.document.frontmatter["title"] == "ADR-0001 — Use SQLite First"
        assert str(created.path) == f"{DECISIONS}/adr-0001_use-sqlite-first.md"

    def test_stating_the_first_number_is_the_same_as_asking(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept("decision", "ADR-0001 — First", workspace=workspace)
        assert str(created.path) == f"{DECISIONS}/adr-0001_first.md"


class TestSeries:
    def test_a_folder_with_one_series_uses_it(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-ops-0003_a.md")
        created = content.create_concept("decision", "Next", workspace=workspace)
        assert created.document.frontmatter["title"] == "ADR-OPS-0004 — Next"
        assert str(created.path) == f"{DECISIONS}/adr-ops-0004_next.md"

    def test_a_folder_with_two_needs_one_named(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-ops-0008_a.md", "adr-web-0007_b.md")
        with pytest.raises(ConceptCreationError, match=r"ops.*web"):
            content.create_concept("decision", "Next", workspace=workspace)

    def test_a_named_series_counts_on_its_own(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-ops-0008_a.md", "adr-web-0007_b.md")
        created = content.create_concept("decision", "Next", workspace=workspace, series="web")
        assert created.document.frontmatter["title"] == "ADR-WEB-0008 — Next"

    def test_a_stated_series_names_it(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-ops-0008_a.md", "adr-web-0007_b.md")
        created = content.create_concept("decision", "ADR-OPS-0009 — Next", workspace=workspace)
        assert str(created.path) == f"{DECISIONS}/adr-ops-0009_next.md"

    def test_a_stated_series_that_contradicts_the_named_one_is_refused(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-ops-0008_a.md")
        with pytest.raises(ConceptCreationError, match="series"):
            content.create_concept(
                "decision", "ADR-OPS-0009 — Next", workspace=workspace, series="web"
            )


class TestOnlyADecisionIsNumbered:
    def test_numbering_another_type_is_refused(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        with pytest.raises(ConceptCreationError, match="decision"):
            content.create_concept("plan", "A Plan", workspace=workspace, numbered=True)
        with pytest.raises(ConceptCreationError, match="decision"):
            content.create_concept("plan", "A Plan", workspace=workspace, series="ops")

    def test_another_type_with_a_number_like_title_is_left_alone(
        self, root: Path, content: ContentService, workspace: ConceptId
    ) -> None:
        existing(root, "adr-0011_a.md")
        created = content.create_concept(
            "research_note",
            "ADR-0033 Follow-up",
            workspace=workspace,
            fields={"lifecycle": "active"},
        )
        assert created.document.frontmatter["title"] == "ADR-0033 Follow-up"
