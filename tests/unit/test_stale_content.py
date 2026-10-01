"""`core/02` section 32: the runtime should detect stale content.

Section 32, *Anti-Drift Rules*, lists stale content among what a maintaining
runtime ought to notice. Without this check a document could pass its own
expiry and `doctor` would call the vault healthy.

`core/02` section 15 adopts OKF `stale_after` and defines stale as
``now >= stale_after``. `schema/vocabulary.py` registers it, the index stores
it, and `search` surfaces it as `is_stale`.

Section 15.2 governs the response: stale content remains available, is visibly
flagged, and is never silently deleted. So this is a WARNING that reports, in
keeping with the rule that validation never repairs -- `stale_after` is a claim
the document's author made about their own content, and the fix is a person
deciding whether it is still true.

A malformed `stale_after` is deliberately not this check's business.
`schema/validation.py` already reports `invalid_timestamp` for every field in
`TIMESTAMP_FIELDS` -- including a value with no offset, which section 7.4
requires -- and `doctor` runs that validation, so skipping a value this check
cannot parse leaves no silent hole.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor

NOTE_ID = "01a04bd1-7357-7002-8bbc-8e82bcb91959"
WORKSPACE_ID = "01a03428-7d75-703a-8b55-58b8d820bbb6"

#: Every test reads the vault at this instant, so "stale" is a property of the
#: fixture rather than of the day the suite happens to run.
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


def write_note(vault: Path, *, stale_after: str | None) -> None:
    field = f'stale_after: "{stale_after}"\n' if stale_after is not None else ""
    (vault / "30_Knowledge" / "Notes" / "a-note.md").write_text(
        f"---\ntype: note\nid: {NOTE_ID}\nschema: never4ga/0.1\n"
        f'title: A Note\ncreated_at: "2026-08-01T12:00:00Z"\n'
        f"{field}---\n\n# A Note\n"
    )


def diagnose(vault: Path) -> Doctor:
    return Doctor(FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock)


def findings(vault: Path) -> dict[str, str]:
    return {f.code: f.message for f in diagnose(vault).diagnose().findings}


class TestContentPastItsOwnExpiry:
    def test_is_reported(self, vault: Path) -> None:
        write_note(vault, stale_after="2026-08-28T12:00:00Z")
        found = findings(vault)
        assert "content_is_stale" in found
        assert "A Note" in found["content_is_stale"]

    def test_the_instant_itself_is_stale(self, vault: Path) -> None:
        """core/02 section 15: `now >= stale_after`, not `>`."""
        write_note(vault, stale_after="2026-08-29T12:00:00Z")
        assert "content_is_stale" in findings(vault)

    def test_content_still_within_its_window_is_not_reported(self, vault: Path) -> None:
        write_note(vault, stale_after="2026-08-30T12:00:00Z")
        assert "content_is_stale" not in findings(vault)

    def test_absence_is_not_staleness(self, vault: Path) -> None:
        """core/02 section 15: absent means no instant was asserted.

        It does not mean the content can never go stale, and it does not mean
        it has. A vault whose documents mostly omit the field must not light up.
        """
        write_note(vault, stale_after=None)
        assert "content_is_stale" not in findings(vault)

    def test_a_naive_timestamp_is_left_to_validation(self, vault: Path) -> None:
        """core/02 section 7.4: every timestamp carries an explicit offset.

        So a value without one is malformed rather than midnight somewhere, and
        guessing UTC would be this check inventing the very thing it exists to
        catch. `validate` already names it.
        """
        write_note(vault, stale_after="2026-08-28T12:00:00")
        found = findings(vault)
        assert "content_is_stale" not in found
        assert "invalid_timestamp" in found

    def test_an_offset_is_honoured_rather_than_ignored(self, vault: Path) -> None:
        """23:30-05:00 on the 29th is 04:30Z on the 30th, which is not yet."""
        write_note(vault, stale_after="2026-08-29T23:30:00-05:00")
        assert "content_is_stale" not in findings(vault)

    def test_an_unparseable_value_is_left_to_validation(self, vault: Path) -> None:
        """schema/validation.py already reports it as `invalid_timestamp`.

        Reporting it twice, under two codes, would make one finding look like
        two problems.
        """
        write_note(vault, stale_after="next tuesday")
        found = findings(vault)
        assert "content_is_stale" not in found
        assert "invalid_timestamp" in found

    def test_it_is_a_warning_that_says_what_to_do(self, vault: Path) -> None:
        write_note(vault, stale_after="2026-08-28T12:00:00Z")
        (finding,) = [
            f for f in diagnose(vault).diagnose().findings if f.code == "content_is_stale"
        ]
        assert finding.severity.value == "warning"
        assert finding.path is not None
        assert finding.repair_hint is not None

    def test_the_message_names_the_instant_it_passed(self, vault: Path) -> None:
        """A finding that says only "stale" sends somebody to `git log`."""
        write_note(vault, stale_after="2026-08-28T12:00:00Z")
        assert "2026-08-28" in findings(vault)["content_is_stale"]


class TestTheVaultStaysUsable:
    def test_a_healthy_vault_reports_nothing(self, vault: Path) -> None:
        """The templates `init` writes must not carry an expired instant."""
        assert "content_is_stale" not in findings(vault)

    def test_stale_content_is_still_counted_as_a_concept(self, vault: Path) -> None:
        """core/02 section 15.2: stale content remains available."""
        write_note(vault, stale_after="2026-08-28T12:00:00Z")
        before = diagnose(vault).diagnose().concept_count
        write_note(vault, stale_after="2026-08-30T12:00:00Z")
        assert diagnose(vault).diagnose().concept_count == before
