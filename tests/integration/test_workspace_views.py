"""The dashboard grows a view when the workspace grows that kind of content.

End to end on a real vault: a fresh workspace starts with the empty views
block, the first goal makes a Goals view appear, coursework makes Units and
Graded Work appear, and a hand-written manifest with no block gains one from
`repair`'s sweep, with every word of its prose intact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import ContentService, VaultInitializer, dashboard


def fixed_clock() -> datetime:
    return datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


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


def manifest_text(root: Path, directory: str) -> str:
    return (root / directory / "workspace.md").read_text(encoding="utf-8")


class TestViewsFollowContent:
    def test_a_fresh_workspace_has_the_block_and_no_views(
        self, content: ContentService, root: Path
    ) -> None:
        content.create_workspace("Example", workspace_type="project")
        text = manifest_text(root, "10_Workspaces/Example")
        assert dashboard.BEGIN in text
        assert "```base" not in text

    def test_the_first_goal_makes_the_goals_view_appear(
        self, content: ContentService, root: Path
    ) -> None:
        created = content.create_workspace("Example", workspace_type="project")
        content.create_concept("goal", "Ship it", workspace=created.concept_id)
        text = manifest_text(root, "10_Workspaces/Example")
        assert "## Goals" in text
        assert '- type == "goal"' in text
        assert "## Plans" not in text

    def test_coursework_makes_units_and_graded_work_appear(
        self, content: ContentService, root: Path
    ) -> None:
        created = content.create_workspace("CS 202", workspace_type="project")
        content.create_concept(
            "course_unit", "Unit 1", workspace=created.concept_id, fields={"unit": 1}
        )
        content.create_concept(
            "course_assignment",
            "Discussion Forum 1",
            workspace=created.concept_id,
            fields={"unit": 1},
        )
        text = manifest_text(root, "10_Workspaces/CS-202")
        assert "## Units" in text
        assert "## Graded Work" in text
        assert 'lifecycle != "graded"' in text

    def test_a_child_workspace_appears_on_the_parent_dashboard(
        self, content: ContentService, root: Path
    ) -> None:
        parent = content.create_workspace("Parent", workspace_type="project")
        content.create_workspace("Child", workspace_type="project", parent=parent.concept_id)
        assert "## Child Workspaces" in manifest_text(root, "10_Workspaces/Parent")
        assert "## Child Workspaces" not in manifest_text(
            root, "10_Workspaces/Parent/Workspaces/Child"
        )

    def test_creating_in_one_workspace_leaves_the_other_alone(
        self, content: ContentService, root: Path
    ) -> None:
        # A creation's blast radius is its own lineage.
        content.create_workspace("Other", workspace_type="project")
        before = manifest_text(root, "10_Workspaces/Other")
        created = content.create_workspace("Example", workspace_type="project")
        content.create_concept("goal", "Ship it", workspace=created.concept_id)
        assert manifest_text(root, "10_Workspaces/Other") == before

    def test_the_manifest_prose_survives_every_refresh(
        self, content: ContentService, root: Path
    ) -> None:
        created = content.create_workspace("Example", workspace_type="project")
        path = root / "10_Workspaces/Example/workspace.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("## Purpose\n", "## Purpose\n\nHand-written purpose prose.\n"),
            encoding="utf-8",
        )
        content.create_concept("goal", "Ship it", workspace=created.concept_id)
        after = manifest_text(root, "10_Workspaces/Example")
        assert "Hand-written purpose prose." in after
        assert "## Goals" in after
