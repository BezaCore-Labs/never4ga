"""A document that tried to have frontmatter and failed is not plain Markdown.

The store reports a file that looks like a concept and cannot be read as one:
unreadable YAML, a missing id, an id that is not a UUIDv7, and an unterminated
frontmatter block.

`parse_document` returns *no* frontmatter when the opening `---` is never
closed on a line of its own -- `---# Heading` fuses the delimiter to the title,
which is what a careless edit or a generator writing `"\\n---\\n" + body` without
the newline produces. Read as plain Markdown, the file would be skipped in
silence: `index` would leave it out, `validate` would not see it, and `doctor`
would stay healthy.

The distinction is cheap and unambiguous: a file whose first line is a `---`
delimiter is *attempting* frontmatter. Plain Markdown does not open that way.
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
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor
from never4ga.services.indexing import IndexService

GOOD = """---
type: knowledge
id: 01a045e8-c469-7122-a7da-0a11b55fed8e
schema: never4ga/0.1
title: A Real Note
created_at: "2026-08-27T12:00:00Z"
---

# A Real Note

Body.
"""

#: The closing delimiter fused to the heading -- what string surgery produces.
UNTERMINATED = GOOD.replace("---\n\n# A Real Note", "---# A Real Note")

#: No frontmatter at all, and none attempted. Legitimate vault content.
PLAIN = "# Just Markdown\n\nSome prose, no frontmatter, and that is fine.\n"


def fixed_clock() -> datetime:
    return datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


def write(vault: Path, name: str, text: str) -> None:
    (vault / "30_Knowledge" / "Notes" / name).write_text(text)


def problems(vault: Path) -> dict[str, str]:
    store = FileSystemMarkdownStore(vault)
    return {problem.path.segments[-1]: problem.code for problem in store.problems()}


def diagnose(vault: Path) -> set[str]:
    return {
        finding.code
        for finding in Doctor(FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault))
        .diagnose()
        .findings
    }


class TestTheStoreNamesIt:
    def test_an_unterminated_frontmatter_block_is_a_problem(self, vault: Path) -> None:
        write(vault, "broken.md", UNTERMINATED)

        assert problems(vault).get("broken.md") == "unterminated_frontmatter"

    def test_plain_markdown_is_still_not_a_problem(self, vault: Path) -> None:
        write(vault, "plain.md", PLAIN)

        assert "plain.md" not in problems(vault)

    def test_a_well_formed_concept_is_still_not_a_problem(self, vault: Path) -> None:
        write(vault, "good.md", GOOD)

        assert "good.md" not in problems(vault)


class TestDoctorSaysSo:
    def test_doctor_reports_it_as_an_error_naming_the_file(self, vault: Path) -> None:
        write(vault, "broken.md", UNTERMINATED)

        assert "unterminated_frontmatter" in diagnose(vault)

    def test_a_healthy_vault_still_reports_nothing(self, vault: Path) -> None:
        write(vault, "good.md", GOOD)
        write(vault, "plain.md", PLAIN)

        assert "unterminated_frontmatter" not in diagnose(vault)


class TestItIsStillNotIndexedAsAConcept:
    """The fix is to *report* it, never to guess at what was meant.

    core/02 section 23 keeps repair explicit, and a delimiter is not something
    to infer: the file might be a concept missing a newline or genuinely be
    Markdown that opens with a horizontal rule. Never4gA says which file and
    why, and a person decides.
    """

    def test_the_file_is_not_read_as_a_concept(self, vault: Path) -> None:
        write(vault, "broken.md", UNTERMINATED)

        store = FileSystemMarkdownStore(vault)
        paths = {document.path.segments[-1] for document in store.iter_documents()}

        assert "broken.md" not in paths

    def test_the_file_is_left_exactly_as_it_was(self, vault: Path) -> None:
        write(vault, "broken.md", UNTERMINATED)
        diagnose(vault)

        assert (vault / "30_Knowledge" / "Notes" / "broken.md").read_text() == UNTERMINATED


class TestPlainMarkdownDoesNotKeepTheIndexStale:
    """Staleness is measured against the population the indexer actually indexes.

    A plain Markdown file is never indexed, correctly, because it is not a
    concept. Counting it when measuring staleness would call it "new" on every
    run. `never4ga capture` writes exactly such a file, so one captured thought
    would leave `doctor` with an `index_is_stale` warning that running
    `never4ga index` could never clear. A warning that cannot be cleared
    teaches the reader to stop reading warnings.
    """

    def service(self, vault: Path) -> IndexService:
        return IndexService(
            documents=FileSystemMarkdownStore(vault),
            metadata=InMemoryMetadataIndex(),
            text=InMemoryTextIndex(),
            graph=InMemoryGraphIndex(),
            state=InMemoryIndexState(),
            clock=fixed_clock,
        )

    def test_an_indexed_vault_with_a_plain_file_is_not_stale(self, vault: Path) -> None:
        write(vault, "good.md", GOOD)
        write(vault, "plain.md", PLAIN)
        service = self.service(vault)
        service.reconcile()

        health = service.health()

        assert [str(path) for path in health.new] == []
        assert not health.is_stale

    def test_a_captured_inbox_note_does_not_make_it_stale(self, vault: Path) -> None:
        (vault / "00_Inbox" / "2026-08-29_a-thought.md").write_text(
            "# A Thought\n\nCaptured 2026-08-29T18:25:44Z.\n\nSomething worth keeping.\n"
        )
        service = self.service(vault)
        service.reconcile()

        assert not service.health().is_stale

    def test_a_file_that_attempted_frontmatter_is_still_counted(self, vault: Path) -> None:
        # The broken case must keep showing up: it is a concept that failed,
        # not prose, and leaving it out of the count would hide it.
        write(vault, "broken.md", UNTERMINATED)
        service = self.service(vault)
        service.reconcile()

        assert [path.segments[-1] for path in service.health().new] == ["broken.md"]
