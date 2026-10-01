"""`core/02` section 32: near-duplicate tags.

Two tags are near-duplicates when they are equal after normalising case and
punctuation. Nothing fuzzier is used, because edit distance cannot tell a
numbered series from a typo:

    milestone-1 ~ milestone-10   (distance 1)
    milestone-1 ~ milestone-2    (distance 1)

Excluding pairs that differ only in digits is not enough either: `milestone-5c`
carries a letter suffix, and `adr` ~ `api` are three-letter acronyms two edits
apart.

Exact match after normalisation collapses case and punctuation and nothing
else, so two tags collide only when they are the same word written two ways.
This reports `CI` beside `ci`, and `ci-cd` beside `ci_cd`, and declines to
guess at anything further.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor

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


def write_note(vault: Path, name: str, tags: list[str]) -> None:
    block = "tags:\n" + "".join(f"  - {tag}\n" for tag in tags) if tags else ""
    uuid = f"01a04e90-{abs(hash(name)) % 10000:04d}-7000-8000-000000000001"
    (vault / "30_Knowledge" / "Notes" / f"{name}.md").write_text(
        f"---\ntype: note\nid: {uuid}\nschema: never4ga/0.1\n"
        f'title: {name}\ncreated_at: "2026-08-01T12:00:00Z"\n{block}---\n\n# {name}\n'
    )


def findings(vault: Path) -> dict[str, str]:
    diagnosis = Doctor(
        FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
    ).diagnose()
    return {f.code: f.message for f in diagnosis.findings}


class TestTagsThatAreOneWordWrittenTwoWays:
    def test_case_alone_is_reported(self, vault: Path) -> None:
        write_note(vault, "one", ["CI"])
        write_note(vault, "two", ["ci"])
        found = findings(vault)
        assert "near_duplicate_tags" in found
        assert "ci" in found["near_duplicate_tags"].lower()

    def test_punctuation_alone_is_reported(self, vault: Path) -> None:
        write_note(vault, "one", ["ci-cd"])
        write_note(vault, "two", ["ci_cd"])
        assert "near_duplicate_tags" in findings(vault)

    def test_case_and_punctuation_together_are_reported(self, vault: Path) -> None:
        write_note(vault, "one", ["Ollama-Embeddings"])
        write_note(vault, "two", ["ollama_embeddings"])
        assert "near_duplicate_tags" in findings(vault)

    def test_the_message_names_both_spellings(self, vault: Path) -> None:
        write_note(vault, "one", ["CI"])
        write_note(vault, "two", ["ci"])
        message = findings(vault)["near_duplicate_tags"]
        assert "CI" in message and "'ci'" in message

    def test_a_pair_is_reported_once(self, vault: Path) -> None:
        write_note(vault, "one", ["CI"])
        write_note(vault, "two", ["ci"])
        codes = [
            f.code
            for f in Doctor(
                FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
            )
            .diagnose()
            .findings
        ]
        assert codes.count("near_duplicate_tags") == 1

    def test_it_is_a_warning_that_says_what_to_do(self, vault: Path) -> None:
        write_note(vault, "one", ["CI"])
        write_note(vault, "two", ["ci"])
        (finding,) = [
            f
            for f in Doctor(
                FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault), now=fixed_clock
            )
            .diagnose()
            .findings
            if f.code == "near_duplicate_tags"
        ]
        assert finding.severity.value == "warning"
        assert finding.repair_hint is not None


class TestTheNumberedSeriesThatReversedTheAnswer:
    """Every pair here is within a small edit distance and is not a duplicate."""

    def test_a_numbered_series_is_not_reported(self, vault: Path) -> None:
        write_note(vault, "one", ["milestone-1", "milestone-10", "milestone-12"])
        write_note(vault, "two", ["milestone-2", "milestone-3", "milestone-9"])
        assert "near_duplicate_tags" not in findings(vault)

    def test_a_lettered_variant_in_the_series_is_not_reported(self, vault: Path) -> None:
        write_note(vault, "one", ["milestone-5c", "milestone-6"])
        assert "near_duplicate_tags" not in findings(vault)

    def test_short_acronyms_two_edits_apart_are_not_reported(self, vault: Path) -> None:
        """`adr` and `api` are both common tags, two edits apart."""
        write_note(vault, "one", ["adr", "api"])
        assert "near_duplicate_tags" not in findings(vault)

    def test_two_ordinary_different_tags_are_not_reported(self, vault: Path) -> None:
        write_note(vault, "one", ["embeddings", "index", "maintenance"])
        assert "near_duplicate_tags" not in findings(vault)

    def test_the_same_tag_used_twice_is_not_a_duplicate(self, vault: Path) -> None:
        write_note(vault, "one", ["ollama"])
        write_note(vault, "two", ["ollama"])
        assert "near_duplicate_tags" not in findings(vault)

    def test_a_fresh_vault_reports_nothing(self, vault: Path) -> None:
        assert "near_duplicate_tags" not in findings(vault)
