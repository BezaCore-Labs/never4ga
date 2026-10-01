"""A document that lost its id is not a document that never had one.

Never4gA's frontmatter lives in the file, so any external tool that rewrites a
document wholesale drops it: the `id` is gone, every link by identity breaks,
and the file goes back to being ordinary prose.

Reported as `untracked_document`, that data loss would be indistinguishable
from a note somebody has simply not adopted yet. One is a warning about a
choice not yet made; the other is a concept that has been destroyed and can
still be recovered from Git.

The index already knows the difference: a path it recorded that is no longer a
concept is `missing`, and a path that exists with no frontmatter is
`untracked`. A path in both was a concept, still exists, and has lost its
identity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.fakes import (
    InMemoryGraphIndex,
    InMemoryIndexState,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.schema import Severity
from never4ga.services import ContentService, VaultInitializer
from never4ga.services.doctor import Doctor
from never4ga.services.indexing import IndexService


def fixed_clock() -> datetime:
    return datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def indexer(vault: Path) -> IndexService:
    return IndexService(
        documents=FileSystemMarkdownStore(vault),
        metadata=InMemoryMetadataIndex(),
        text=InMemoryTextIndex(),
        graph=InMemoryGraphIndex(),
        state=InMemoryIndexState(),
    )


def findings(vault: Path, index: IndexService) -> dict[str, list[str]]:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault),
        FileSystemMarkdownStore(vault),
        index=index.health(),
    ).diagnose()
    found: dict[str, list[str]] = {}
    for finding in diagnosis.findings:
        found.setdefault(finding.code, []).append(str(finding.path))
    return found


def severity_of(vault: Path, index: IndexService, code: str) -> Severity:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault),
        FileSystemMarkdownStore(vault),
        index=index.health(),
    ).diagnose()
    return next(f.severity for f in diagnosis.findings if f.code == code)


def strip_frontmatter(path: Path) -> None:
    """What a tool that rewrites a file wholesale leaves behind."""
    text = path.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    path.write_text(body.lstrip("\n"), encoding="utf-8")


class TestLosingAnIdentityIsItsOwnFinding:
    def test_a_rewritten_concept_is_reported_as_lost_not_untracked(self, vault: Path) -> None:
        service = ContentService(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
        )
        created = service.create_knowledge("Adopted Then Rewritten")
        index = indexer(vault)
        index.reconcile()

        strip_frontmatter(vault / str(created.path))

        found = findings(vault, index)
        assert str(created.path) in found.get("identity_lost", [])
        assert str(created.path) not in found.get("untracked_document", [])

    def test_it_is_an_error_because_it_is_data_loss(self, vault: Path) -> None:
        service = ContentService(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
        )
        created = service.create_knowledge("Adopted Then Rewritten")
        index = indexer(vault)
        index.reconcile()
        strip_frontmatter(vault / str(created.path))

        assert severity_of(vault, index, "identity_lost") is Severity.ERROR

    def test_the_message_names_the_identity_that_was_lost(self, vault: Path) -> None:
        service = ContentService(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
        )
        created = service.create_knowledge("Adopted Then Rewritten")
        index = indexer(vault)
        index.reconcile()
        strip_frontmatter(vault / str(created.path))

        diagnosis = Doctor(
            FileSystemVaultFileStore(vault),
            FileSystemMarkdownStore(vault),
            index=index.health(),
        ).diagnose()
        finding = next(f for f in diagnosis.findings if f.code == "identity_lost")
        assert str(created.concept_id) in finding.message
        # Git is where it comes back from, and the hint has to say so: the id
        # cannot be re-minted, only recovered.
        assert "git" in (finding.repair_hint or "").lower()


class TestProseIsStillProse:
    def test_a_note_never_adopted_is_still_only_untracked(self, vault: Path) -> None:
        (vault / "30_Knowledge" / "Notes" / "by-hand.md").write_text(
            "# By Hand\n\nnever adopted\n", encoding="utf-8"
        )
        index = indexer(vault)
        index.reconcile()

        found = findings(vault, index)
        assert "30_Knowledge/Notes/by-hand.md" in found.get("untracked_document", [])
        assert "identity_lost" not in found

    def test_a_deleted_concept_is_not_reported_as_lost(self, vault: Path) -> None:
        """It is in `missing` but not in `untracked`: there is no file."""
        service = ContentService(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
        )
        created = service.create_knowledge("Deleted Outright")
        index = indexer(vault)
        index.reconcile()

        (vault / str(created.path)).unlink()

        assert "identity_lost" not in findings(vault, index)

    def test_without_an_index_nothing_is_claimed(self, vault: Path) -> None:
        """No index means no memory of what was a concept, so no accusation."""
        (vault / "30_Knowledge" / "Notes" / "by-hand.md").write_text(
            "# By Hand\n\nprose\n", encoding="utf-8"
        )
        diagnosis = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        codes = {finding.code for finding in diagnosis.findings}
        assert "identity_lost" not in codes
        assert "untracked_document" in codes
