"""Two rules about whether a document's declared scope matches where it sits.

`core/02` section 32 lists *orphan workspace content*;
`details/data-indexing-maintenance.md` section 21 adds *parent/child workspace
mismatch*, and section 23 names the same thing among the four contradictions it
is willing to call deterministic.

`schema/validation.py` checks `workspace` and `parent` only for *shape* --
`invalid_workspace_reference` and `invalid_parent_reference` fire when the
value is not a canonical UUID. These rules ask whether the id resolves to
anything, and whether what it resolves to agrees with the location.

**The directory half of the orphan rule is scoped to `10_Workspaces/`.**
Archived material keeps `workspace:` pointing at the workspace whose history it
is, while sitting under `90_Archive/Workspaces/<Name>/`; `core/01` section 13
makes that the specified arrangement for a rolled-up year. A rule that read the
archive would call every such document misfiled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor

PARENT_ID = "01a03428-7cbf-74eb-9a1a-fc12755a0c56"
CHILD_ID = "01a03428-7d75-703a-8b55-58b8d820bbb6"
STRANGER_ID = "01a04e70-3333-7000-8000-000000000003"
NOTE_ID = "01a04e70-4444-7000-8000-000000000004"

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return NOW


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def write_workspace(
    vault: Path, directory: str, *, concept_id: str, title: str, parent: str | None = None
) -> None:
    path = vault / directory
    path.mkdir(parents=True, exist_ok=True)
    parent_field = f"parent: {parent}\n" if parent else ""
    (path / "workspace.md").write_text(
        f"---\ntype: workspace\nid: {concept_id}\nschema: never4ga/0.1\n"
        f'title: {title}\ncreated_at: "2026-08-01T12:00:00Z"\n{parent_field}---\n\n# {title}\n'
    )


def write_note(vault: Path, directory: str, *, workspace: str | None) -> None:
    path = vault / directory
    path.mkdir(parents=True, exist_ok=True)
    field = f"workspace: {workspace}\n" if workspace else ""
    (path / "a-note.md").write_text(
        f"---\ntype: note\nid: {NOTE_ID}\nschema: never4ga/0.1\n"
        f'title: A Note\ncreated_at: "2026-08-01T12:00:00Z"\n{field}---\n\n# A Note\n'
    )


def write_area(vault: Path, name: str, *, concept_id: str, title: str) -> None:
    """A life area manifest (core/01 section 7, core/02 section 21.3)."""
    path = vault / "20_Life" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "area.md").write_text(
        f"---\ntype: life_area\nid: {concept_id}\nschema: never4ga/0.1\n"
        f'title: {title}\ncreated_at: "2026-08-01T12:00:00Z"\nlifecycle: active\n'
        f"---\n\n# {title}\n"
    )


def findings(vault: Path) -> dict[str, str]:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
    ).diagnose()
    return {f.code: f.message for f in diagnosis.findings}


def a_parent(vault: Path) -> None:
    write_workspace(vault, "10_Workspaces/Acme", concept_id=PARENT_ID, title="Acme")


class TestAWorkspaceWhoseParentDisagrees:
    def test_a_child_naming_a_parent_that_is_not_a_workspace_is_reported(self, vault: Path) -> None:
        a_parent(vault)
        write_workspace(
            vault,
            "10_Workspaces/Acme/Workspaces/Widget",
            concept_id=CHILD_ID,
            title="Widget",
            parent=STRANGER_ID,
        )
        found = findings(vault)
        assert "workspace_parent_mismatch" in found
        assert "Widget" in found["workspace_parent_mismatch"]

    def test_a_correctly_nested_child_is_not(self, vault: Path) -> None:
        a_parent(vault)
        write_workspace(
            vault,
            "10_Workspaces/Acme/Workspaces/Widget",
            concept_id=CHILD_ID,
            title="Widget",
            parent=PARENT_ID,
        )
        assert "workspace_parent_mismatch" not in findings(vault)

    def test_a_child_naming_the_wrong_workspace_is_reported(self, vault: Path) -> None:
        """It resolves, and it is a workspace, and it is still not the parent."""
        a_parent(vault)
        write_workspace(vault, "10_Workspaces/Other", concept_id=STRANGER_ID, title="Other")
        write_workspace(
            vault,
            "10_Workspaces/Acme/Workspaces/Widget",
            concept_id=CHILD_ID,
            title="Widget",
            parent=STRANGER_ID,
        )
        assert "workspace_parent_mismatch" in findings(vault)

    def test_a_nested_child_naming_no_parent_is_reported(self, vault: Path) -> None:
        a_parent(vault)
        write_workspace(
            vault, "10_Workspaces/Acme/Workspaces/Widget", concept_id=CHILD_ID, title="Widget"
        )
        assert "workspace_parent_mismatch" in findings(vault)

    def test_a_top_level_workspace_with_no_parent_is_not(self, vault: Path) -> None:
        a_parent(vault)
        assert "workspace_parent_mismatch" not in findings(vault)

    def test_a_top_level_workspace_claiming_a_parent_is_reported(self, vault: Path) -> None:
        """core/01 section 6: a child lives under its parent's `Workspaces/`.

        Declaring a parent while sitting at the top contradicts the location,
        which is section 23's *workspace parent mismatch*.
        """
        a_parent(vault)
        write_workspace(
            vault, "10_Workspaces/Other", concept_id=CHILD_ID, title="Other", parent=PARENT_ID
        )
        assert "workspace_parent_mismatch" in findings(vault)

    def test_it_is_a_warning_that_says_what_to_do(self, vault: Path) -> None:
        a_parent(vault)
        write_workspace(
            vault,
            "10_Workspaces/Acme/Workspaces/Widget",
            concept_id=CHILD_ID,
            title="Widget",
            parent=STRANGER_ID,
        )
        (finding,) = [
            f
            for f in Doctor(
                FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
            )
            .diagnose()
            .findings
            if f.code == "workspace_parent_mismatch"
        ]
        assert finding.severity.value == "warning"
        assert finding.path is not None
        assert finding.repair_hint is not None


class TestContentWhoseWorkspaceDoesNotExist:
    def test_is_reported(self, vault: Path) -> None:
        a_parent(vault)
        write_note(vault, "10_Workspaces/Acme/Research", workspace=STRANGER_ID)
        found = findings(vault)
        assert "orphan_workspace_content" in found
        assert "A Note" in found["orphan_workspace_content"]

    def test_content_naming_a_real_workspace_is_not(self, vault: Path) -> None:
        a_parent(vault)
        write_note(vault, "10_Workspaces/Acme/Research", workspace=PARENT_ID)
        assert "orphan_workspace_content" not in findings(vault)

    def test_content_naming_no_workspace_is_not(self, vault: Path) -> None:
        """core/02 does not require one. A note in 30_Knowledge belongs to nobody."""
        write_note(vault, "30_Knowledge/Notes", workspace=None)
        assert "orphan_workspace_content" not in findings(vault)

    def test_it_is_a_warning_that_says_what_to_do(self, vault: Path) -> None:
        a_parent(vault)
        write_note(vault, "10_Workspaces/Acme/Research", workspace=STRANGER_ID)
        (finding,) = [
            f
            for f in Doctor(
                FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
            )
            .diagnose()
            .findings
            if f.code == "orphan_workspace_content"
        ]
        assert finding.severity.value == "warning"
        assert finding.path is not None
        assert finding.repair_hint is not None


class TestContentFiledUnderTheWrongWorkspace:
    def test_a_note_in_one_workspace_naming_another_is_reported(self, vault: Path) -> None:
        a_parent(vault)
        write_workspace(vault, "10_Workspaces/Other", concept_id=STRANGER_ID, title="Other")
        write_note(vault, "10_Workspaces/Acme/Research", workspace=STRANGER_ID)
        found = findings(vault)
        assert "misfiled_workspace_content" in found
        assert "Acme" in found["misfiled_workspace_content"]

    def test_a_note_in_its_own_workspace_is_not(self, vault: Path) -> None:
        a_parent(vault)
        write_note(vault, "10_Workspaces/Acme/Research", workspace=PARENT_ID)
        assert "misfiled_workspace_content" not in findings(vault)

    def test_a_note_outside_any_workspace_is_not(self, vault: Path) -> None:
        """`30_Knowledge/Notes/` may name the workspace it was written for."""
        a_parent(vault)
        write_note(vault, "30_Knowledge/Notes", workspace=PARENT_ID)
        assert "misfiled_workspace_content" not in findings(vault)

    def test_archived_material_naming_its_live_workspace_is_not(self, vault: Path) -> None:
        """core/01 section 13: a rolled-up year keeps its workspace.

        A rolled-up year moves to `90_Archive/Workspaces/<Name>/Logs/<YYYY>/`
        and keeps `workspace:` pointing at the live workspace, because that is
        whose history it is. Reading the archive would call every one misfiled.
        """
        a_parent(vault)
        write_note(vault, "90_Archive/Workspaces/Acme/Logs/2025", workspace=PARENT_ID)
        assert "misfiled_workspace_content" not in findings(vault)

    def test_a_child_workspaces_content_is_not_the_parents(self, vault: Path) -> None:
        """Workspaces nest, so the innermost directory owns the document."""
        a_parent(vault)
        write_workspace(
            vault,
            "10_Workspaces/Acme/Workspaces/Widget",
            concept_id=CHILD_ID,
            title="Widget",
            parent=PARENT_ID,
        )
        write_note(vault, "10_Workspaces/Acme/Workspaces/Widget/Research", workspace=CHILD_ID)
        assert "misfiled_workspace_content" not in findings(vault)


AREA_ID = "01a04f5f-5581-737b-80b2-16181bec2321"


class TestAWorkspaceWhoseParentIsALifeArea:
    """core/03 section 30: a workspace's parent may be a life area.

    `20_Life/<Area>/Workspaces/` works exactly as a workspace's own does, and
    the workspace there names the *area* as `parent` (core/03 section 30). The
    area is never itself a workspace, so the id a child resolves to has to come
    from the life-area manifest rather than from the workspace map.
    """

    def test_a_course_naming_its_area_is_not_reported(self, vault: Path) -> None:
        write_area(vault, "Education", concept_id=AREA_ID, title="Education")
        write_workspace(
            vault,
            "20_Life/Education/Workspaces/MATH-101",
            concept_id=CHILD_ID,
            title="MATH-101",
            parent=AREA_ID,
        )
        assert "workspace_parent_mismatch" not in findings(vault)

    def test_a_course_naming_no_parent_is_reported(self, vault: Path) -> None:
        write_area(vault, "Education", concept_id=AREA_ID, title="Education")
        write_workspace(
            vault,
            "20_Life/Education/Workspaces/MATH-101",
            concept_id=CHILD_ID,
            title="MATH-101",
        )
        found = findings(vault)
        assert "workspace_parent_mismatch" in found
        assert "MATH-101" in found["workspace_parent_mismatch"]

    def test_a_course_naming_the_wrong_area_is_reported(self, vault: Path) -> None:
        write_area(vault, "Education", concept_id=AREA_ID, title="Education")
        write_workspace(vault, "10_Workspaces/Other", concept_id=STRANGER_ID, title="Other")
        write_workspace(
            vault,
            "20_Life/Education/Workspaces/MATH-101",
            concept_id=CHILD_ID,
            title="MATH-101",
            parent=STRANGER_ID,
        )
        assert "workspace_parent_mismatch" in findings(vault)

    def test_a_workspace_nested_inside_a_course_names_the_course(self, vault: Path) -> None:
        write_area(vault, "Education", concept_id=AREA_ID, title="Education")
        write_workspace(
            vault,
            "20_Life/Education/Workspaces/Northfield",
            concept_id=PARENT_ID,
            title="Northfield",
            parent=AREA_ID,
        )
        write_workspace(
            vault,
            "20_Life/Education/Workspaces/Northfield/Workspaces/MATH-101",
            concept_id=CHILD_ID,
            title="MATH-101",
            parent=PARENT_ID,
        )
        assert "workspace_parent_mismatch" not in findings(vault)


class TestContentInsideAnAreaWorkspace:
    def test_a_note_declaring_a_different_workspace_is_reported(self, vault: Path) -> None:
        write_area(vault, "Education", concept_id=AREA_ID, title="Education")
        write_workspace(vault, "10_Workspaces/Acme", concept_id=PARENT_ID, title="Acme")
        write_workspace(
            vault,
            "20_Life/Education/Workspaces/MATH-101",
            concept_id=CHILD_ID,
            title="MATH-101",
            parent=AREA_ID,
        )
        write_note(vault, "20_Life/Education/Workspaces/MATH-101/Research", workspace=PARENT_ID)
        assert "misfiled_workspace_content" in findings(vault)

    def test_a_note_declaring_the_workspace_it_sits_in_is_not(self, vault: Path) -> None:
        write_area(vault, "Education", concept_id=AREA_ID, title="Education")
        write_workspace(
            vault,
            "20_Life/Education/Workspaces/MATH-101",
            concept_id=CHILD_ID,
            title="MATH-101",
            parent=AREA_ID,
        )
        write_note(vault, "20_Life/Education/Workspaces/MATH-101/Research", workspace=CHILD_ID)
        assert "misfiled_workspace_content" not in findings(vault)
