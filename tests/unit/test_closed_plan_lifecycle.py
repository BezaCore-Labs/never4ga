"""A plan that something `closes` should not still call itself open.

A completion report records the plan it finishes with a `closes` relation
(`core/02` section 17.2). `doctor` reads that relation and reports a closed plan
whose lifecycle is still open. It never repairs it: a plan's lifecycle is a
claim its author makes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor

PLAN_ID = "01a04434-7ef1-7521-b306-85b52696200f"
REPORT_ID = "01a040fe-2017-7212-8c34-4b2b495bc727"
WORKSPACE_ID = "01a03428-7d75-703a-8b55-58b8d820bbb6"


def fixed_clock() -> datetime:
    return datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    (root / "10_Workspaces" / "Thing" / "Plans").mkdir(parents=True)
    (root / "10_Workspaces" / "Thing" / "Logs").mkdir(parents=True)
    (root / "10_Workspaces" / "Thing" / "workspace.md").write_text(
        f"---\ntype: workspace\nid: {WORKSPACE_ID}\nschema: never4ga/0.1\n"
        'title: Thing\ncreated_at: "2026-08-28T12:00:00Z"\nworkspace_type: product\n'
        "lifecycle: active\n---\n\n# Thing\n"
    )
    return root


def write_plan(vault: Path, lifecycle: str) -> None:
    (vault / "10_Workspaces" / "Thing" / "Plans" / "a-plan.md").write_text(
        f"---\ntype: plan\nid: {PLAN_ID}\nschema: never4ga/0.1\n"
        f'title: A Plan\ncreated_at: "2026-08-28T12:00:00Z"\n'
        f"workspace: {WORKSPACE_ID}\nlifecycle: {lifecycle}\n---\n\n# A Plan\n"
    )


def write_report(vault: Path, *, closes: str | None = PLAN_ID) -> None:
    relations = f"relations:\n  - type: closes\n    target: {closes}\n" if closes else ""
    (vault / "10_Workspaces" / "Thing" / "Logs" / "done.md").write_text(
        f"---\ntype: activity_log\nid: {REPORT_ID}\nschema: never4ga/0.1\n"
        f'title: Done\ncreated_at: "2026-08-28T12:00:00Z"\n'
        f'occurred_at: "2026-08-28T12:00:00Z"\nworkspace: {WORKSPACE_ID}\n'
        f"{relations}---\n\n# Done\n"
    )


def findings(vault: Path) -> dict[str, str]:
    diagnosis = Doctor(FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)).diagnose()
    return {f.code: f.message for f in diagnosis.findings}


class TestAClosedPlanThatStillCallsItselfOpen:
    @pytest.mark.parametrize("lifecycle", ["draft", "active"])
    def test_is_reported(self, vault: Path, lifecycle: str) -> None:
        write_plan(vault, lifecycle)
        write_report(vault)
        found = findings(vault)
        assert "closed_plan_is_open" in found
        assert "A Plan" in found["closed_plan_is_open"]
        assert lifecycle in found["closed_plan_is_open"]

    @pytest.mark.parametrize("lifecycle", ["completed", "abandoned"])
    def test_a_terminal_lifecycle_is_not_reported(self, vault: Path, lifecycle: str) -> None:
        write_plan(vault, lifecycle)
        write_report(vault)
        assert "closed_plan_is_open" not in findings(vault)

    def test_a_plan_nothing_closes_is_not_reported(self, vault: Path) -> None:
        """The check reads the relation. It does not guess from age or silence."""
        write_plan(vault, "draft")
        write_report(vault, closes=None)
        assert "closed_plan_is_open" not in findings(vault)

    def test_an_unknown_lifecycle_is_tolerated(self, vault: Path) -> None:
        """core/02 section 20: unknown lifecycle values are tolerated.

        A vault with its own vocabulary must not be told its plans are open
        because Never4gA does not recognise the word.
        """
        write_plan(vault, "shipped")
        write_report(vault)
        assert "closed_plan_is_open" not in findings(vault)

    def test_a_closes_pointing_nowhere_is_not_a_crash(self, vault: Path) -> None:
        write_plan(vault, "draft")
        write_report(vault, closes="01a04434-0000-7000-8000-000000000000")
        assert "closed_plan_is_open" not in findings(vault)

    def test_a_malformed_target_is_not_a_crash(self, vault: Path) -> None:
        write_plan(vault, "draft")
        write_report(vault, closes="not-an-identity")
        assert "closed_plan_is_open" not in findings(vault)

    def test_it_is_a_warning_and_says_what_to_do(self, vault: Path) -> None:
        write_plan(vault, "draft")
        write_report(vault)
        diagnosis = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        (finding,) = [f for f in diagnosis.findings if f.code == "closed_plan_is_open"]
        assert finding.severity.value == "warning"
        assert finding.repair_hint is not None
        assert "completed" in finding.repair_hint
