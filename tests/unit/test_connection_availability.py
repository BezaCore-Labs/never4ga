"""Whether a configured tracker answers.

`details/data-indexing-maintenance.md` §21 lists an unavailable configured PM
connection as a maintenance rule. The check is opt-in and off by default:
`doctor --check-connections` pings each configured tracker, and without it
`doctor` never touches the network. That keeps a diagnosis fast, offline-safe
and deterministic, so it can run on the §22 schedule and in CI, and a tracker
being down never turns a healthy vault into a failing diagnosis.

The external system is authoritative for its own state, Never4gA's copy is
derived and dated, and a stale answer must never pass as a current one.

So the finding is ephemeral. A recorded "unavailable" is a claim about the
network at one instant, and a ledger row saying so would still read as true
tomorrow. Reachability is re-established by asking, never by remembering.

And the default run reports nothing about reachability rather than reporting
from the cache. The cache records `fetched_at` on work items, which says
something was cached then, not that the tracker answered then. Presenting one
as the other would pass a stale answer as current.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.fakes import FakeRepositoryLocator
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.ports.work_management import ProviderHealth
from never4ga.schema import Severity
from never4ga.services import VaultInitializer
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


def diagnose(vault: Path, health: dict[str, ProviderHealth] | None = None) -> list[Finding]:
    return list(
        Doctor(
            FileSystemVaultFileStore(vault),
            FileSystemMarkdownStore(vault),
            index=IndexHealth(),
            repositories=FakeRepositoryLocator(),
            adapters=(),
            connection_health=health,
        )
        .diagnose()
        .findings
    )


class TestWhenItRuns:
    def test_an_unreachable_tracker_is_reported(self, vault: Path) -> None:
        findings = diagnose(
            vault, {"work": ProviderHealth(available=False, detail="connection refused")}
        )
        (finding,) = [f for f in findings if f.code == "connection_unavailable"]
        assert "work" in finding.message
        assert "connection refused" in finding.message

    def test_a_reachable_one_says_nothing(self, vault: Path) -> None:
        findings = diagnose(vault, {"work": ProviderHealth(available=True)})
        assert not [f for f in findings if f.code == "connection_unavailable"]

    def test_it_is_a_warning_rather_than_an_error(self, vault: Path) -> None:
        """The vault is not wrong because a server is down.

        `core/05` §19: a failed optional subsystem must not destroy the service,
        and nothing in the vault depends on a tracker being reachable.
        """
        (finding,) = [
            f
            for f in diagnose(vault, {"work": ProviderHealth(available=False)})
            if f.code == "connection_unavailable"
        ]
        assert finding.severity is Severity.WARNING

    def test_its_hint_names_no_verb(self, vault: Path) -> None:
        # Nothing Never4gA runs fixes somebody else's server, and a hint naming
        # a command would make `repair` treat it as mechanical.
        (finding,) = [
            f
            for f in diagnose(vault, {"work": ProviderHealth(available=False)})
            if f.code == "connection_unavailable"
        ]
        assert finding.repair_hint is not None
        assert not finding.repair_hint.startswith("run `never4ga")


class TestWhenItDoesNot:
    def test_the_default_run_says_nothing_about_reachability(self, vault: Path) -> None:
        assert not [f for f in diagnose(vault) if f.code == "connection_unavailable"]

    def test_and_the_diagnosis_is_still_complete(self, vault: Path) -> None:
        """The opposite of the index and the adapters, deliberately.

        Those make a run partial when they are absent, because a check that did
        not run must not resolve findings. This one is *off by default*, so
        treating its absence as partial would stop every ordinary `doctor` from
        recording anything at all.

        What makes that safe is the finding being ephemeral: there is nothing in
        the ledger for a later run to wrongly resolve.
        """
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


class TestItIsNeverRemembered:
    def test_the_finding_is_marked_ephemeral(self, vault: Path) -> None:
        """A stored "unavailable" still reads as true tomorrow.

        That is a stale answer passing as a current one, with a `detected_at`
        making it look authoritative. Reachability is re-established by
        asking.
        """
        (finding,) = [
            f
            for f in diagnose(vault, {"work": ProviderHealth(available=False)})
            if f.code == "connection_unavailable"
        ]
        assert finding.ephemeral

    def test_ordinary_findings_are_not(self) -> None:
        assert not Finding("broken_link", "x", Severity.WARNING).ephemeral
