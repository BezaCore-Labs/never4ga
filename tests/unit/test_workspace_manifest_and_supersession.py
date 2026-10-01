"""Two exact `core/02` section 32 rules that `doctor` enforces.

**Missing workspace manifest.** `core/01` section 4 keeps semantic manifests
separate from reserved OKF navigation, so a workspace is `workspace.md` and not
`index.md`. An ERROR: without a manifest a workspace has no identity, nothing
can be filed against it, and `workspace resolve` cannot reach it.

**Superseded decision without a replacement.** `core/02` section 21.11 requires
a superseded decision to identify what replaced it. A WARNING, because the
decision record is still true history; what is missing is the pointer forward.

The inverse is deliberately not checked. A decision may be superseded by
something outside this vault, and section 32 does not ask for a bidirectional
link.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor

WORKSPACE_ID = "01a03428-7d75-703a-8b55-58b8d820bbb6"
DECISION_ID = "01a04e70-1111-7000-8000-000000000001"
REPLACEMENT_ID = "01a04e70-2222-7000-8000-000000000002"

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


def write_workspace(vault: Path, directory: str, *, manifest: bool = True) -> None:
    """Create a workspace directory, with or without its manifest."""
    path = vault / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "Context").mkdir(exist_ok=True)
    if manifest:
        (path / "workspace.md").write_text(
            f"---\ntype: workspace\nid: {WORKSPACE_ID}\nschema: never4ga/0.1\n"
            f'title: A Workspace\ncreated_at: "2026-08-01T12:00:00Z"\n---\n\n# A Workspace\n'
        )


def write_decision(
    vault: Path, *, lifecycle: str, superseded_by: str | None, replacement: bool = False
) -> None:
    directory = vault / "10_Workspaces" / "Acme"
    directory.mkdir(parents=True, exist_ok=True)
    relation = (
        f"relations:\n  - type: superseded_by\n    target: {superseded_by}\n"
        if superseded_by
        else ""
    )
    (directory / "a-decision.md").write_text(
        f"---\ntype: decision\nid: {DECISION_ID}\nschema: never4ga/0.1\n"
        f'title: A Decision\ncreated_at: "2026-08-01T12:00:00Z"\n'
        f"lifecycle: {lifecycle}\n{relation}---\n\n# A Decision\n"
    )
    if replacement:
        (directory / "the-replacement.md").write_text(
            f"---\ntype: decision\nid: {REPLACEMENT_ID}\nschema: never4ga/0.1\n"
            f'title: The Replacement\ncreated_at: "2026-08-02T12:00:00Z"\n'
            f"lifecycle: accepted\n---\n\n# The Replacement\n"
        )


def findings(vault: Path) -> dict[str, str]:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
    ).diagnose()
    return {f.code: f.message for f in diagnosis.findings}


def codes(vault: Path) -> list[str]:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
    ).diagnose()
    return [f.code for f in diagnosis.findings]


class TestAWorkspaceWithoutAManifest:
    def test_is_reported(self, vault: Path) -> None:
        write_workspace(vault, "10_Workspaces/Acme", manifest=False)
        found = findings(vault)
        assert "missing_workspace_manifest" in found
        assert "10_Workspaces/Acme" in found["missing_workspace_manifest"]

    def test_a_workspace_with_one_is_not(self, vault: Path) -> None:
        write_workspace(vault, "10_Workspaces/Acme")
        assert "missing_workspace_manifest" not in findings(vault)

    def test_it_is_an_error_because_the_workspace_has_no_identity(self, vault: Path) -> None:
        write_workspace(vault, "10_Workspaces/Acme", manifest=False)
        (finding,) = [
            f
            for f in Doctor(
                FileSystemVaultFileStore(vault),
                FileSystemMarkdownStore(vault),
                now=fixed_clock,
            )
            .diagnose()
            .findings
            if f.code == "missing_workspace_manifest"
        ]
        assert finding.severity.value == "error"
        assert finding.repair_hint is not None

    def test_a_child_workspace_is_checked_too(self, vault: Path) -> None:
        """core/01 section 6: children nest through `Workspaces/`."""
        write_workspace(vault, "10_Workspaces/Acme")
        write_workspace(vault, "10_Workspaces/Acme/Workspaces/Widget", manifest=False)
        assert (
            "10_Workspaces/Acme/Workspaces/Widget" in findings(vault)["missing_workspace_manifest"]
        )

    def test_the_container_directory_is_not_a_workspace(self, vault: Path) -> None:
        """`Acme/Workspaces/` holds workspaces; it is not one."""
        write_workspace(vault, "10_Workspaces/Acme")
        (vault / "10_Workspaces" / "Acme" / "Workspaces").mkdir(parents=True, exist_ok=True)
        assert "missing_workspace_manifest" not in findings(vault)

    def test_a_section_directory_is_not_a_workspace(self, vault: Path) -> None:
        """`Context/`, `Logs/`, `Plans/` are sections, not workspaces."""
        write_workspace(vault, "10_Workspaces/Acme")
        for section in ("Logs", "Plans", "Decisions"):
            (vault / "10_Workspaces" / "Acme" / section).mkdir(exist_ok=True)
        assert "missing_workspace_manifest" not in findings(vault)

    def test_the_archive_is_not_checked(self, vault: Path) -> None:
        """`90_Archive/Workspaces/<Name>/` is not an archived workspace.

        It is where a *live* workspace's detached material goes. core/01
        section 13 makes that the specified use: a closed year's logs move to
        `90_Archive/Workspaces/<Name>/Logs/<YYYY>/` while the workspace carries
        on.

        Requiring a manifest there would ask for a second document claiming to
        be the same workspace. The path cannot tell the two cases apart, and
        the only thing that would is the manifest whose absence is the
        question, so the check declines to guess.
        """
        write_workspace(vault, "90_Archive/Workspaces/BezaCore-Labs", manifest=False)
        assert "missing_workspace_manifest" not in findings(vault)

    def test_a_fresh_vault_reports_nothing(self, vault: Path) -> None:
        assert "missing_workspace_manifest" not in findings(vault)


class TestAWorkspaceUnderALifeAreaWithoutAManifest:
    """The rule also applies under a life area (core/01 section 7).

    A bounded pursuit under `20_Life/<Area>/Workspaces/` owes a manifest for
    the same reason as one under `10_Workspaces/`: without one the directory
    has no identity.
    """

    def test_is_reported(self, vault: Path) -> None:
        write_workspace(vault, "20_Life/Education/Workspaces/MATH-101", manifest=False)
        found = findings(vault)
        assert "missing_workspace_manifest" in found
        assert "20_Life/Education/Workspaces/MATH-101" in found["missing_workspace_manifest"]

    def test_one_with_a_manifest_is_not(self, vault: Path) -> None:
        write_workspace(vault, "20_Life/Education/Workspaces/MATH-101")
        assert "missing_workspace_manifest" not in findings(vault)

    def test_the_area_itself_owes_no_workspace_manifest(self, vault: Path) -> None:
        """It is carried, not pursued: `area.md` is its manifest, not this one."""
        (vault / "20_Life/Gardening/spring-planting").mkdir(parents=True, exist_ok=True)
        assert "missing_workspace_manifest" not in findings(vault)


class TestASupersededDecisionWithoutItsReplacement:
    def test_is_reported(self, vault: Path) -> None:
        write_decision(vault, lifecycle="superseded", superseded_by=None)
        found = findings(vault)
        assert "superseded_decision_without_replacement" in found
        assert "A Decision" in found["superseded_decision_without_replacement"]

    def test_one_naming_its_replacement_is_not(self, vault: Path) -> None:
        write_decision(
            vault, lifecycle="superseded", superseded_by=REPLACEMENT_ID, replacement=True
        )
        assert "superseded_decision_without_replacement" not in findings(vault)

    def test_a_decision_at_another_lifecycle_is_not(self, vault: Path) -> None:
        write_decision(vault, lifecycle="accepted", superseded_by=None)
        assert "superseded_decision_without_replacement" not in findings(vault)

    def test_it_is_a_warning_that_says_what_to_do(self, vault: Path) -> None:
        write_decision(vault, lifecycle="superseded", superseded_by=None)
        (finding,) = [
            f
            for f in Doctor(
                FileSystemVaultFileStore(vault),
                FileSystemMarkdownStore(vault),
                now=fixed_clock,
            )
            .diagnose()
            .findings
            if f.code == "superseded_decision_without_replacement"
        ]
        assert finding.severity.value == "warning"
        assert finding.path is not None
        assert finding.repair_hint is not None

    def test_a_replacement_that_does_not_exist_is_one_finding_not_two(self, vault: Path) -> None:
        """`unresolved_relation` owns the dangling target; this owns the absence.

        A superseded decision naming a replacement that is not in the vault has
        satisfied section 21.11: it identified one. That the target is missing
        is a different defect with its own code, and reporting both would make
        one problem look like two.
        """
        write_decision(vault, lifecycle="superseded", superseded_by=REPLACEMENT_ID)
        assert "superseded_decision_without_replacement" not in codes(vault)

    def test_a_fresh_vault_reports_nothing(self, vault: Path) -> None:
        assert "superseded_decision_without_replacement" not in findings(vault)
