"""One diagnosis, one pass over the corpus.

The cost of a diagnosis is dominated by reading and parsing the vault, not by
the rules: validating every document against Schema v0.1 is cheap. So every
check works from the ``concepts`` list ``diagnose`` already loaded, including
the stray ``system_manifest`` scan and the connection findings.

Any new check that calls ``self._documents.iter_documents()`` instead of using
the ``concepts`` it is handed adds a full pass, silently: every finding would
still be right. Timing tests are flaky and measure the machine, so this counts
passes instead.

The maintenance tiers of `details/data-indexing-maintenance.md` section 22
depend on this. A tier is cheap when it can work from documents already in
hand.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from never4ga.adapters.fakes import FakeRepositoryLocator
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import ConceptId
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Diagnosis, Doctor
from never4ga.services.indexing import IndexHealth


class CountingStore(FileSystemMarkdownStore):
    """A document store that remembers how often it was walked."""

    passes: int = 0

    def iter_documents(self) -> Iterator[StoredDocument]:
        self.passes += 1
        return super().iter_documents()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Test Vault"
    )
    notes = root / "30_Knowledge" / "Notes"
    notes.mkdir(parents=True, exist_ok=True)
    for name in ("one", "two", "three"):
        (notes / f"{name}.md").write_text(
            f"---\ntype: knowledge\nid: {ConceptId.new()}\nschema: never4ga/0.1\n"
            f'title: {name.title()}\ncreated_at: "2026-08-23T12:00:00Z"\n---\n\nbody\n',
            encoding="utf-8",
        )
    return root


def a_connection(vault: Path, name: str, **extra: str) -> None:
    """A connection definition -- `core/02` section 21.19 puts it here."""
    directory = vault / "50_System" / "Integrations"
    directory.mkdir(parents=True, exist_ok=True)
    fields = "".join(f"{key}: {value}\n" for key, value in extra.items())
    (directory / f"{name}.md").write_text(
        f"---\ntype: integration\nid: {ConceptId.new()}\nschema: never4ga/0.1\n"
        f'title: {name.title()}\ncreated_at: "2026-08-23T12:00:00Z"\n'
        f"connection: {name}\nprovider: openproject\n"
        f"base_url: https://pm.example.dev\nproject_ref: never4ga\n{fields}"
        "---\n\nbody\n",
        encoding="utf-8",
    )


def a_second_manifest(vault: Path) -> None:
    """A stray `system_manifest`, which is what `_check_identity` looks for."""
    (vault / "30_Knowledge" / "Notes" / "stray.md").write_text(
        f"---\ntype: system_manifest\nid: {ConceptId.new()}\nschema: never4ga/0.1\n"
        'title: Stray\ncreated_at: "2026-08-23T12:00:00Z"\n---\n\nbody\n',
        encoding="utf-8",
    )


def diagnose(vault: Path) -> tuple[Diagnosis, int]:
    documents = CountingStore(vault)
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault),
        documents,
        index=IndexHealth(),
        repositories=FakeRepositoryLocator(),
    ).diagnose()
    return diagnosis, documents.passes


class TestThePassCount:
    def test_a_clean_vault_is_read_once(self, vault: Path) -> None:
        _, passes = diagnose(vault)
        assert passes == 1

    def test_a_vault_with_connections_is_read_once(self, vault: Path) -> None:
        # `ConnectionRegistry.findings()` must use the documents in hand, not
        # walk the whole vault again to find a handful of connections.
        a_connection(vault, "work")
        _, passes = diagnose(vault)
        assert passes == 1

    def test_a_vault_with_a_stray_manifest_is_read_once(self, vault: Path) -> None:
        a_second_manifest(vault)
        _, passes = diagnose(vault)
        assert passes == 1


class TestNothingElseChanged:
    """The pass count is the only thing that may move."""

    def test_a_stray_manifest_is_still_reported(self, vault: Path) -> None:
        a_second_manifest(vault)
        diagnosis, _ = diagnose(vault)
        assert "duplicate_vault_identity" in {f.code for f in diagnosis.findings}

    def test_the_vault_identity_is_still_the_manifest_s(self, vault: Path) -> None:
        documents = FileSystemMarkdownStore(vault)
        from never4ga.layout import SYSTEM_MANIFEST

        manifest = documents.get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        diagnosis, _ = diagnose(vault)
        assert diagnosis.vault_id == manifest.concept_id

    def test_a_leaked_secret_in_a_connection_is_still_reported(self, vault: Path) -> None:
        a_connection(vault, "work", api_token="hunter2")
        diagnosis, _ = diagnose(vault)
        codes = {f.code for f in diagnosis.findings}
        assert any(code.startswith("connection") for code in codes)

    def test_a_clean_connection_is_still_silent(self, vault: Path) -> None:
        a_connection(vault, "work")
        diagnosis, _ = diagnose(vault)
        assert not [f for f in diagnosis.findings if f.code.startswith("connection")]

    def test_a_vault_with_no_manifest_still_says_so(self, tmp_path: Path) -> None:
        # The manifest is fetched by path rather than found in the corpus. One
        # `get_by_path` is free, and deriving it from the walk would tie "is
        # there a vault identity" to whether the walk's eligibility rules
        # happened to admit that one file.
        bare = tmp_path / "bare"
        bare.mkdir()
        diagnosis, _ = diagnose(bare)
        assert "no_vault_identity" in {f.code for f in diagnosis.findings}
