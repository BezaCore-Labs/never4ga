"""`doctor` reports adapter drift.

`details/data-indexing-maintenance.md` §24 lists "Agent adapters" and "Skill
sync" among what `doctor` reports, and §21 names stale adapter deployment as a
first-class rule. A person asking what is wrong should get one answer, and the
§22 scheduler can only surface what `doctor` reports.

`adapters doctor` remains the detailed per-client view, and `adapters sync`
remains the verb that fixes drift.

The codes are kept apart because the responses differ. A Skill that is merely
not deployed is fixed by `sync --apply`. A collision is a Never4gA name already
occupied by something Never4gA does not own, and `core/09` §11 forbids
overwriting it. One is a chore and the other is a decision, and a single code
would lose the distinction `is_actionable` preserves.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from never4ga.adapters.fakes import FakeRepositoryLocator
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer
from never4ga.services.adapters import AdapterFinding
from never4ga.services.doctor import Doctor, Finding
from never4ga.services.indexing import IndexHealth


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Test Vault"
    )
    return root


def diagnose(vault: Path, adapters: Sequence[AdapterFinding] | None) -> list[Finding]:
    return list(
        Doctor(
            FileSystemVaultFileStore(vault),
            FileSystemMarkdownStore(vault),
            index=IndexHealth(),
            repositories=FakeRepositoryLocator(),
            adapters=adapters,
        )
        .diagnose()
        .findings
    )


class TestWhatDoctorNowSays:
    def test_a_stale_deployment_is_reported(self, vault: Path) -> None:
        findings = diagnose(
            vault, [AdapterFinding("stale", "claude-code", "never4ga-wrap", "the vault's is newer")]
        )
        assert "stale_adapter_deployment" in {f.code for f in findings}

    def test_one_no_longer_canonical_is_the_same_rule(self, vault: Path) -> None:
        findings = diagnose(
            vault, [AdapterFinding("to_remove", "codex", "never4ga-old", "gone from the vault")]
        )
        assert "stale_adapter_deployment" in {f.code for f in findings}

    def test_one_never_deployed_is_not_drift(self, vault: Path) -> None:
        """A Skill that was never deployed is not drift.

        Every new install starts with nothing deployed. The rule is stale
        adapter deployment: something deployed that no longer matches.
        `adapters doctor` still lists what is not deployed.
        """
        findings = diagnose(
            vault, [AdapterFinding("not_deployed", "codex", "never4ga-wrap", "absent")]
        )
        assert not [f for f in findings if f.code.startswith("adapter")]
        assert "stale_adapter_deployment" not in {f.code for f in findings}

    def test_a_collision_is_its_own_code(self, vault: Path) -> None:
        # Never4gA never overwrites unmanaged capability (`core/09` §11).
        # `sync --apply` will not clear this, so it must not read like the
        # findings it will clear.
        findings = diagnose(
            vault, [AdapterFinding("collision", "claude-code", "never4ga-wrap", "already there")]
        )
        codes = {f.code for f in findings}
        assert "adapter_collision" in codes
        assert "stale_adapter_deployment" not in codes

    def test_a_client_that_is_not_installed_is_not_a_finding(self, vault: Path) -> None:
        # Not having Codex on this machine is not a problem with the vault.
        # `adapters doctor` says so because that is its job; `doctor` reporting
        # it would be a warning nobody can clear.
        findings = diagnose(
            vault, [AdapterFinding("client_not_installed", "codex", "", "not installed")]
        )
        assert not [f for f in findings if f.code.startswith("adapter")]

    def test_the_finding_names_the_client_and_the_skill(self, vault: Path) -> None:
        findings = diagnose(
            vault, [AdapterFinding("stale", "claude-code", "never4ga-wrap", "the vault's is newer")]
        )
        (finding,) = [f for f in findings if f.code == "stale_adapter_deployment"]
        assert "claude-code" in finding.message and "never4ga-wrap" in finding.message

    def test_it_points_at_the_verb_that_fixes_it(self, vault: Path) -> None:
        findings = diagnose(
            vault, [AdapterFinding("stale", "claude-code", "never4ga-wrap", "newer")]
        )
        (finding,) = [f for f in findings if f.code == "stale_adapter_deployment"]
        assert finding.repair_hint is not None
        assert "adapters sync" in finding.repair_hint

    def test_a_collision_does_not_point_at_a_verb(self, vault: Path) -> None:
        # Its hint must not name a command, or `repair` would treat it as
        # mechanical, and Never4gA will not resolve it either way (`core/09`
        # §11). `tests/unit/test_repair_plan.py` reads these hints.
        findings = diagnose(
            vault, [AdapterFinding("collision", "claude-code", "never4ga-wrap", "already there")]
        )
        (finding,) = [f for f in findings if f.code == "adapter_collision"]
        assert finding.repair_hint is not None
        assert not finding.repair_hint.startswith("run `never4ga")


class TestWhenNothingIsWired:
    def test_no_adapters_means_the_check_does_not_run(self, vault: Path) -> None:
        assert not [f for f in diagnose(vault, None) if f.code.startswith("adapter")]

    def test_and_the_diagnosis_is_not_complete(self, vault: Path) -> None:
        """A root that forgot to wire this must not resolve what it never ran.

        The same rule the index and the repository locator follow: a check that
        did not run is not a check that found nothing, and `Diagnosis.complete`
        is what stops the ledger acting on the difference.
        """
        assert (
            not Doctor(
                FileSystemVaultFileStore(vault),
                FileSystemMarkdownStore(vault),
                index=IndexHealth(),
                repositories=FakeRepositoryLocator(),
            )
            .diagnose()
            .complete
        )

    def test_an_empty_list_is_an_answer_rather_than_an_absence(self, vault: Path) -> None:
        # Nothing drifted is a result, as with `mappings=()`.
        assert (
            Doctor(
                FileSystemVaultFileStore(vault),
                FileSystemMarkdownStore(vault),
                index=IndexHealth(),
                repositories=FakeRepositoryLocator(),
                adapters=(),
            )
            .diagnose()
            .complete
        )
