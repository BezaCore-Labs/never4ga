"""`doctor` names a config key that no longer does anything.

Never4gA reads machine-local config and never writes it (core/05 section 9),
so it cannot delete a dead block. What it can do is say the block is dead.

A table for a retired feature, such as `[embedding]`, still reads as if the
feature were live. Someone reading the config can then blame that feature for
a problem it has nothing to do with.

The finding is a warning rather than an error: a stale key breaks nothing, and
the vault is untouched by it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.configuration import RetiredSetting
from never4ga.schema import Severity
from never4ga.services import Doctor, VaultInitializer
from never4ga.services.doctor import Finding

EMBEDDING = RetiredSetting("embedding", "the vector lane is retired. Delete the block")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    files = FileSystemVaultFileStore(tmp_path)
    documents = FileSystemMarkdownStore(tmp_path)
    VaultInitializer(files, documents).initialize("Test Vault")
    documents.refresh()
    return tmp_path


def findings(vault: Path, settings: tuple[RetiredSetting, ...]) -> list[Finding]:
    doctor = Doctor(
        FileSystemVaultFileStore(vault),
        FileSystemMarkdownStore(vault),
        configuration=settings,
    )
    return [f for f in doctor.diagnose().findings if f.code == "config_names_a_retired_setting"]


class TestRetiredConfigIsReported:
    def test_a_retired_key_is_a_warning(self, vault: Path) -> None:
        (finding,) = findings(vault, (EMBEDDING,))
        assert finding.severity is Severity.WARNING

    def test_the_message_names_the_key_and_the_reason(self, vault: Path) -> None:
        (finding,) = findings(vault, (EMBEDDING,))
        assert "embedding" in finding.message
        assert "vector lane is retired" in finding.message

    def test_nothing_retired_is_nothing_reported(self, vault: Path) -> None:
        assert findings(vault, ()) == []

    def test_the_finding_carries_no_path(self, vault: Path) -> None:
        # The config lives outside the vault, and a finding whose path pointed
        # into it would send `repair` looking for a document that is not there.
        (finding,) = findings(vault, (EMBEDDING,))
        assert finding.path is None

    def test_it_is_not_repairable(self, vault: Path) -> None:
        # Repair writes only the vault. This file is neither in the vault nor
        # Never4gA's to write (core/05 section 9), so the fix is a person
        # deleting the block and the hint has to say so.
        from never4ga.services.repair import RepairPlanner

        (finding,) = findings(vault, (EMBEDDING,))
        plan = RepairPlanner().plan([finding])
        assert plan.actions == ()
        assert [f.code for f in plan.unrepairable] == ["config_names_a_retired_setting"]
