"""A finding says which document has the problem.

`details/data-indexing-maintenance.md` section 20 gives a finding a
``document_id`` and a ``path``: the two ways a ledger row says where the problem
is. For ``broken_link`` and ``unresolved_relation`` both name the document that
holds the link or relation, not its target. The target stays in the message.

If ``path`` named the target, several documents linking to the same missing file
would produce identical findings, and the ledger would store them as one row.
The id matters because `core/06` section 3 makes it the identity that survives a
rename. The fixtures give two documents the same broken link, so one row per
document is distinguishable from one row in total.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.fakes import FakeRepositoryLocator
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.index_state import LinkRecord
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor
from never4ga.services.indexing import IndexHealth, UnresolvedRelation
from never4ga.services.maintenance import fingerprint

ONE = ConceptId.parse("01a04bd1-7357-7002-8bbc-8e82bcb91959")
TWO = ConceptId.parse("01a04bd1-7357-7002-8bbc-8e82bcb9195a")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Test Vault"
    )
    notes = root / "30_Knowledge" / "Notes"
    notes.mkdir(parents=True, exist_ok=True)
    for name, concept_id in (("one", ONE), ("two", TWO)):
        (notes / f"{name}.md").write_text(
            f"---\ntype: knowledge\nid: {concept_id}\nschema: never4ga/0.1\n"
            f'title: {name.title()}\ncreated_at: "2026-08-23T12:00:00Z"\n'
            f"---\n\nsee [gone](nowhere.md)\n",
            encoding="utf-8",
        )
    return root


def findings_for(vault: Path, health: IndexHealth, code: str) -> list[object]:
    doctor = Doctor(
        FileSystemVaultFileStore(vault),
        FileSystemMarkdownStore(vault),
        index=health,
        repositories=FakeRepositoryLocator(),
    )
    return [f for f in doctor.diagnose().findings if f.code == code]


class TestABrokenLink:
    @pytest.fixture
    def health(self) -> IndexHealth:
        gone = VaultPath.parse("30_Knowledge/Notes/nowhere.md")
        return IndexHealth(
            broken_links=(
                LinkRecord(source=ONE, target_path=gone),
                LinkRecord(source=TWO, target_path=gone),
            )
        )

    def test_it_names_the_document_holding_the_link(self, vault: Path, health: IndexHealth) -> None:
        paths = {f.path for f in findings_for(vault, health, "broken_link")}  # type: ignore[attr-defined]
        assert paths == {
            VaultPath.parse("30_Knowledge/Notes/one.md"),
            VaultPath.parse("30_Knowledge/Notes/two.md"),
        }

    def test_it_carries_that_document_s_id(self, vault: Path, health: IndexHealth) -> None:
        ids = {f.document_id for f in findings_for(vault, health, "broken_link")}  # type: ignore[attr-defined]
        assert ids == {ONE, TWO}

    def test_the_message_still_names_the_missing_target(
        self, vault: Path, health: IndexHealth
    ) -> None:
        # The reader still learns which target to fix.
        for finding in findings_for(vault, health, "broken_link"):
            assert "30_Knowledge/Notes/nowhere.md" in finding.message  # type: ignore[attr-defined]

    def test_two_documents_are_two_problems(self, vault: Path, health: IndexHealth) -> None:
        # The ledger stores identical findings as one row, so these must differ.
        marks = {fingerprint(f) for f in findings_for(vault, health, "broken_link")}  # type: ignore[arg-type]
        assert len(marks) == 2

    def test_a_link_by_name_names_the_document_too(self, vault: Path) -> None:
        # A bare `[[Note]]` has no target path, so the source is the only path
        # the finding can carry.
        health = IndexHealth(broken_links=(LinkRecord(source=ONE, target_name="Nothing"),))
        (finding,) = findings_for(vault, health, "broken_link")
        assert finding.path == VaultPath.parse("30_Knowledge/Notes/one.md")  # type: ignore[attr-defined]
        assert "Nothing" in finding.message  # type: ignore[attr-defined]


class TestAnUnresolvedRelation:
    @pytest.fixture
    def health(self) -> IndexHealth:
        return IndexHealth(
            unresolved_relations=(
                UnresolvedRelation(source=ONE, relation_type="supersedes", target=TWO),
                UnresolvedRelation(source=TWO, relation_type="supersedes", target=ONE),
            )
        )

    def test_it_names_the_document_declaring_the_relation(
        self, vault: Path, health: IndexHealth
    ) -> None:
        paths = {f.path for f in findings_for(vault, health, "unresolved_relation")}  # type: ignore[attr-defined]
        assert paths == {
            VaultPath.parse("30_Knowledge/Notes/one.md"),
            VaultPath.parse("30_Knowledge/Notes/two.md"),
        }

    def test_it_carries_that_document_s_id(self, vault: Path, health: IndexHealth) -> None:
        ids = {f.document_id for f in findings_for(vault, health, "unresolved_relation")}  # type: ignore[attr-defined]
        assert ids == {ONE, TWO}


class TestADocumentTheIndexNoLongerHas:
    def test_the_finding_survives_without_a_path(self, vault: Path) -> None:
        """The index can name a source the vault no longer holds.

        A link recorded before its document was deleted, and `doctor` run
        before `index` caught up. Reporting nothing would hide a real broken
        link; the honest answer is the finding with no path, which is what the
        column being nullable is for.
        """
        stranger = ConceptId.parse("01a04bd1-7357-7002-8bbc-8e82bcb9195b")
        health = IndexHealth(
            broken_links=(
                LinkRecord(source=stranger, target_path=VaultPath.parse("30_Knowledge/x.md")),
            )
        )
        (finding,) = findings_for(vault, health, "broken_link")
        assert finding.path is None  # type: ignore[attr-defined]
        assert finding.document_id == stranger  # type: ignore[attr-defined]
