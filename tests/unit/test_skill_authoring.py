"""`skills new`: a Skill we author starts from the template, with its provenance.

The template is a generator and never a gate. These tests hold the verb to
that: what it writes, what it records in the same act, what it refuses, and
that nothing here checks a Skill it did not write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.vendoring import OriginKind
from never4ga.services import scaffold
from never4ga.services.skill_authoring import SkillAuthor, SkillAuthoringError
from never4ga.services.skill_library import SkillLibrary
from never4ga.services.vault import VaultInitializer
from never4ga.services.vendoring import VendoringService

ACTOR = "claude-code/claude-fable-5-1"


def _clock() -> datetime:
    return datetime(2026, 9, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def files(root: Path) -> FileSystemVaultFileStore:
    return FileSystemVaultFileStore(root)


@pytest.fixture
def documents(root: Path, files: FileSystemVaultFileStore) -> FileSystemMarkdownStore:
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents, now=_clock).initialize("Test Vault")
    documents.refresh()
    return documents


@pytest.fixture
def vendoring(
    files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore
) -> VendoringService:
    return VendoringService(files, documents)


@pytest.fixture
def author(files: FileSystemVaultFileStore, vendoring: VendoringService) -> SkillAuthor:
    return SkillAuthor(files, vendoring, actor=ACTOR, now=_clock)


class TestWhatItWrites:
    def test_the_skill_lands_in_its_own_directory(self, author: SkillAuthor, root: Path) -> None:
        authored = author.author("release-notes", "Writes release notes from merged pull requests.")
        assert str(authored.path) == "50_System/Skills/release-notes/SKILL.md"
        assert (root / str(authored.path)).is_file()

    def test_the_name_and_description_are_filled_in(self, author: SkillAuthor, root: Path) -> None:
        authored = author.author("release-notes", "Writes release notes from merged pull requests.")
        text = (root / str(authored.path)).read_text()
        assert text.startswith("---\nname: release-notes\n")
        assert 'description: "Writes release notes from merged pull requests."' in text
        assert "# release-notes\n" in text

    def test_it_is_ours_and_a_draft(self, author: SkillAuthor, root: Path) -> None:
        # core/04 section 21's metadata, as strings. `draft` rather than
        # `reviewed`: nobody has read a Skill that is all placeholders.
        text = (root / str(author.author("thing", "Does a thing.").path)).read_text()
        assert 'never4ga-source: "authored"' in text
        assert 'never4ga-review-status: "draft"' in text
        assert "never4ga-seed-hash" not in text

    def test_a_description_with_quotes_stays_valid_yaml(
        self, author: SkillAuthor, root: Path
    ) -> None:
        text = (root / str(author.author("q", 'Says "hello": twice.').path)).read_text()
        assert 'description: "Says \\"hello\\": twice."' in text

    def test_the_rest_is_left_for_the_author(self, author: SkillAuthor, root: Path) -> None:
        text = (root / str(author.author("thing", "Does a thing.").path)).read_text()
        assert "## When to use it" in text
        assert "## Never" in text
        assert "<" in text.split("---\n", 2)[2]


class TestTheRecordIsWrittenInTheSameAct:
    def test_provenance_exists_with_a_tree_digest(
        self, author: SkillAuthor, vendoring: VendoringService, root: Path
    ) -> None:
        # A freehand Skill with no record would be reported by `doctor` as
        # permanent drift.
        authored = author.author("release-notes", "Writes release notes.")
        assert (root / str(authored.provenance)).is_file()
        record = vendoring.read("50_System/Skills/release-notes")
        assert record is not None
        assert record.integrity.tree_digest
        assert record.origin.kind is OriginKind.AUTHORED

    def test_the_record_matches_what_is_on_disk(
        self, author: SkillAuthor, vendoring: VendoringService
    ) -> None:
        author.author("release-notes", "Writes release notes.")
        state = vendoring.state_of("50_System/Skills/release-notes")
        assert state.recorded is not None and state.present is not None
        assert state.present.tree_digest == state.recorded.integrity.tree_digest

    def test_the_record_names_who_started_it(self, author: SkillAuthor, root: Path) -> None:
        authored = author.author("thing", "Does a thing.")
        assert f"by: {ACTOR}" in (root / str(authored.provenance)).read_text()


class TestWhatItRefuses:
    def test_a_name_outside_the_agent_skills_format(self, author: SkillAuthor) -> None:
        for bad in ("Release Notes", "release_notes", "-release", "release-", "a" * 65):
            with pytest.raises(SkillAuthoringError, match="not a Skill name"):
                author.author(bad, "x")

    def test_a_shipped_skills_name(self, author: SkillAuthor) -> None:
        with pytest.raises(SkillAuthoringError, match="ships"):
            author.author("never4ga-startup", "x")

    def test_a_skill_that_is_already_there(self, author: SkillAuthor) -> None:
        author.author("thing", "Does a thing.")
        with pytest.raises(SkillAuthoringError, match="already exists"):
            author.author("thing", "Does a thing again.")

    def test_an_empty_description(self, author: SkillAuthor) -> None:
        with pytest.raises(SkillAuthoringError, match="description"):
            author.author("thing", "   ")


class TestTheTemplateIsTheVaults:
    def test_an_edited_vault_template_is_what_is_used(
        self, author: SkillAuthor, root: Path
    ) -> None:
        # core/04 section 20: the vault is canonical for a Skill, and the
        # template it starts from is honoured the way an edited concept
        # template is.
        template = root / "50_System" / "Templates" / "skill.md"
        template.write_text(template.read_text().replace("## Never", "## House rules"))
        text = (root / str(author.author("thing", "Does a thing.").path)).read_text()
        assert "## House rules" in text
        assert "## Never" not in text

    def test_a_vault_without_the_template_gets_the_shipped_one(
        self, author: SkillAuthor, root: Path
    ) -> None:
        (root / "50_System" / "Templates" / "skill.md").unlink()
        text = (root / str(author.author("thing", "Does a thing.").path)).read_text()
        assert "## Never" in text


class TestItIsAGeneratorNotAGate:
    def test_an_authored_skill_is_not_listed_against_the_shipped_ones(
        self, author: SkillAuthor, files: FileSystemVaultFileStore
    ) -> None:
        # `skills status` compares the vault against what the product ships.
        # A Skill of ours is not the product's to report on.
        author.author("thing", "Does a thing.")
        assert "thing" not in {status.name for status in SkillLibrary(files).status()}

    def test_the_template_is_seeded_and_kept_current(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        assert (
            root / "50_System" / "Templates" / "skill.md"
        ).read_text() == scaffold.SKILL_TEMPLATE
        by_name = {status.name: status for status in SkillLibrary(files).template_status()}
        assert by_name["skill.md"].state.value == "current"
