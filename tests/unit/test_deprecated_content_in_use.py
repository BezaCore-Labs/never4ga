"""`core/02` section 32: deprecated content still used as current.

Also `details/data-indexing-maintenance.md` section 21, as *deprecated document
referenced by active context*.

Two definitions decide what this check is, and both reuse existing rules:

**Deprecated** is a lifecycle value that says the document has been retired,
across whichever vocabulary its type registers: `superseded`, `abandoned`,
`cancelled`, `rejected`, `retired`. A *completed* plan is not deprecated -- it
finished, and depending on finished work is ordinary.

**Used as current** is a *subject* relation from a live document.
`domain/relations.py` defines `NON_CORROBORATING` as the derived edges plus the
lifecycle edges, because status edges are not statements about subject matter.

That set is exactly what this check must ignore. `superseded_by` and `closes`
exist to point at retired material; a Markdown link does too, and the vault is
full of prose that legitimately cites a superseded ADR to explain how a decision
was reached. Reading those would report every history section in the vault.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor

OLD_ID = "01a04e80-1111-7000-8000-000000000001"
LIVE_ID = "01a04e80-2222-7000-8000-000000000002"

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
    (root / "30_Knowledge" / "Notes").mkdir(parents=True, exist_ok=True)
    return root


def write_old(vault: Path, *, lifecycle: str) -> None:
    (vault / "30_Knowledge" / "Notes" / "the-old-one.md").write_text(
        f"---\ntype: note\nid: {OLD_ID}\nschema: never4ga/0.1\n"
        f'title: The Old One\ncreated_at: "2026-08-01T12:00:00Z"\n'
        f"lifecycle: {lifecycle}\n---\n\n# The Old One\n"
    )


def write_user(
    vault: Path, *, relation: str | None, lifecycle: str = "active", target: str = OLD_ID
) -> None:
    block = f"relations:\n  - type: {relation}\n    target: {target}\n" if relation else ""
    (vault / "30_Knowledge" / "Notes" / "the-live-one.md").write_text(
        f"---\ntype: note\nid: {LIVE_ID}\nschema: never4ga/0.1\n"
        f'title: The Live One\ncreated_at: "2026-08-02T12:00:00Z"\n'
        f"lifecycle: {lifecycle}\n{block}---\n\n# The Live One\n"
    )


def findings(vault: Path) -> dict[str, str]:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
    ).diagnose()
    return {f.code: f.message for f in diagnosis.findings}


class TestALiveDocumentDependingOnARetiredOne:
    @pytest.mark.parametrize(
        "lifecycle", ["superseded", "abandoned", "cancelled", "rejected", "retired"]
    )
    def test_every_retired_lifecycle_is_reported(self, vault: Path, lifecycle: str) -> None:
        write_old(vault, lifecycle=lifecycle)
        write_user(vault, relation="depends_on")
        found = findings(vault)
        assert "deprecated_content_in_use" in found
        assert "The Live One" in found["deprecated_content_in_use"]
        assert "The Old One" in found["deprecated_content_in_use"]

    @pytest.mark.parametrize("relation", ["depends_on", "implements", "applies_to", "supports"])
    def test_every_subject_relation_counts(self, vault: Path, relation: str) -> None:
        write_old(vault, lifecycle="superseded")
        write_user(vault, relation=relation)
        assert "deprecated_content_in_use" in findings(vault)

    def test_a_completed_target_is_not_deprecated(self, vault: Path) -> None:
        """Finished is not retired. Depending on completed work is ordinary."""
        write_old(vault, lifecycle="completed")
        write_user(vault, relation="depends_on")
        assert "deprecated_content_in_use" not in findings(vault)

    def test_an_active_target_is_not_reported(self, vault: Path) -> None:
        write_old(vault, lifecycle="active")
        write_user(vault, relation="depends_on")
        assert "deprecated_content_in_use" not in findings(vault)

    def test_it_is_a_warning_that_names_both(self, vault: Path) -> None:
        write_old(vault, lifecycle="superseded")
        write_user(vault, relation="depends_on")
        (finding,) = [
            f
            for f in Doctor(
                FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
            )
            .diagnose()
            .findings
            if f.code == "deprecated_content_in_use"
        ]
        assert finding.severity.value == "warning"
        assert finding.path is not None
        assert finding.repair_hint is not None


class TestWhatMustNeverTriggerIt:
    @pytest.mark.parametrize("relation", ["superseded_by", "closes"])
    def test_a_lifecycle_relation_does_not(self, vault: Path, relation: str) -> None:
        """`domain/relations.py`: these exist to point at retired material.

        `superseded_by` names what replaced a document and `closes` names
        finished work. Reporting them would make the correct way of recording
        supersession into a finding.
        """
        write_old(vault, lifecycle="superseded")
        write_user(vault, relation=relation)
        assert "deprecated_content_in_use" not in findings(vault)

    def test_a_retired_document_depending_on_another_does_not(self, vault: Path) -> None:
        """Old material may cite old material. Only live use is a problem."""
        write_old(vault, lifecycle="superseded")
        write_user(vault, relation="depends_on", lifecycle="superseded")
        assert "deprecated_content_in_use" not in findings(vault)

    def test_a_markdown_link_does_not(self, vault: Path) -> None:
        """Prose cites superseded decisions constantly, to explain history.

        `links_to` is a derived edge -- nobody asserted it as a dependency --
        and reading it would report every history section in the vault.
        """
        write_old(vault, lifecycle="superseded")
        (vault / "30_Knowledge" / "Notes" / "the-live-one.md").write_text(
            f"---\ntype: note\nid: {LIVE_ID}\nschema: never4ga/0.1\n"
            f'title: The Live One\ncreated_at: "2026-08-02T12:00:00Z"\n'
            f"lifecycle: active\n---\n\n# The Live One\n\n"
            f"We used to do it [the old way](the-old-one.md), and stopped.\n"
        )
        assert "deprecated_content_in_use" not in findings(vault)

    def test_a_relation_to_a_missing_document_does_not(self, vault: Path) -> None:
        """`unresolved_relation` owns that, and one problem reports once."""
        write_user(vault, relation="depends_on", target="01a04e80-9999-7000-8000-000000000009")
        assert "deprecated_content_in_use" not in findings(vault)

    def test_an_unknown_lifecycle_is_not_treated_as_deprecated(self, vault: Path) -> None:
        """core/02 section 20 requires unknown values to be tolerated.

        A vault with its own vocabulary must not have its documents called
        retired because Never4gA does not know the word.
        """
        write_old(vault, lifecycle="mothballed")
        write_user(vault, relation="depends_on")
        assert "deprecated_content_in_use" not in findings(vault)

    def test_a_fresh_vault_reports_nothing(self, vault: Path) -> None:
        assert "deprecated_content_in_use" not in findings(vault)
