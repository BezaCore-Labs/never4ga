"""Adoption out of foreign material keeps what the writer wrote.

A note in foreign material carries frontmatter written to its writer's own
scheme, not Never4gA's. Every key whose value the concept does not carry
unchanged is kept verbatim under `extensions.adopted` and reported, and nothing
is mapped to a registered value (core/02 section 3.3).

A hand-written document already inside a root was written for Never4gA, so a
value that does not fit is still refused.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.schema import ValidationLevel, validate_document
from never4ga.services import ConceptCreationError, ContentService, VaultInitializer

PILE = {
    "Obsidian/active.md": "---\nstatus: active\ntags: [garden]\n---\n# Active\n",
    "Obsidian/book.md": "---\ntype: book\nstatus: reading\nauthor: Le Guin\n---\n# Book\n",
    "Obsidian/titled.md": "---\ntitle: The Writer's Title\n---\n# Titled\n",
    "Obsidian/owned.md": "---\nschema: obsidian/1\ngenerated: by hand\n---\n# Owned\n",
    "Obsidian/tag.md": "---\ntags: garden\n---\n# Tag\n",
    "Obsidian/plain.md": "---\nrating: 4\n---\n# Plain\n",
    "Obsidian/mine.md": (
        "---\nstatus: done\nextensions:\n  mine:\n    bucket: weekly\n---\n# Mine\n"
    ),
    "Obsidian/taken.md": "---\nextensions:\n  adopted:\n    status: x\n---\n# Taken\n",
    "Obsidian/flat.md": "---\nextensions: nope\n---\n# Flat\n",
    "Obsidian/declared.md": "---\ntype: map\nstatus: wip\n---\n# Declared\n",
}


def fixed_clock() -> datetime:
    return datetime(2026, 9, 21, 20, 0, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for relative, text in PILE.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def content(root: Path) -> ContentService:
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents, now=fixed_clock).initialize("Test Vault")
    documents.refresh()
    return ContentService(files, documents, now=fixed_clock, actor="claude-code/test")


def path(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


def valid(frontmatter: dict[str, object], at: VaultPath) -> bool:
    return not validate_document(at, frontmatter, level=ValidationLevel.STRICT).errors


class TestAValueThatDoesNotFit:
    def test_it_is_set_aside_rather_than_refused(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/active.md"), concept_type="knowledge")
        frontmatter = adopted.document.frontmatter
        assert frontmatter["extensions"]["adopted"] == {"status": "active"}
        assert "status" not in frontmatter
        assert valid(dict(frontmatter), adopted.path)

    def test_it_is_reported(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/active.md"), concept_type="knowledge")
        assert adopted.set_aside == ("status",)

    def test_a_value_that_fits_is_carried_and_not_set_aside(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/active.md"), concept_type="knowledge")
        assert adopted.document.frontmatter["tags"] == ["garden"]
        assert "tags" not in adopted.document.frontmatter["extensions"]["adopted"]

    def test_a_single_tag_made_a_list_is_not_a_loss(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/tag.md"), concept_type="knowledge")
        assert adopted.document.frontmatter["tags"] == ["garden"]
        assert adopted.set_aside == ()

    def test_nothing_to_set_aside_writes_no_namespace(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/plain.md"), concept_type="knowledge")
        assert "extensions" not in adopted.document.frontmatter
        assert adopted.set_aside == ()
        assert adopted.document.frontmatter["rating"] == 4

    def test_it_lands_in_the_file(self, content: ContentService, root: Path) -> None:
        adopted = content.adopt_concept(path("Obsidian/active.md"), concept_type="knowledge")
        text = (root / str(adopted.path)).read_text(encoding="utf-8")
        assert "adopted:\n    status: active\n" in text


class TestAnUnregisteredType:
    def test_a_named_type_adopts_it_and_the_writer_s_type_is_kept(
        self, content: ContentService
    ) -> None:
        adopted = content.adopt_concept(path("Obsidian/book.md"), concept_type="knowledge")
        frontmatter = adopted.document.frontmatter
        assert frontmatter["type"] == "knowledge"
        assert frontmatter["extensions"]["adopted"] == {"type": "book", "status": "reading"}
        assert frontmatter["author"] == "Le Guin"
        assert set(adopted.set_aside) == {"type", "status"}

    def test_without_a_type_it_is_treated_as_declaring_none(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError) as raised:
            content.adopt_concept(path("Obsidian/book.md"))
        assert "is not a registered type" not in str(raised.value)
        assert raised.value.candidates == ("knowledge", "map")

    def test_a_folder_decides_it(self, content: ContentService) -> None:
        adopted = content.adopt_concept(
            path("Obsidian/book.md"), in_directory=path("30_Knowledge/Maps")
        )
        assert adopted.document.frontmatter["type"] == "map"
        assert adopted.document.frontmatter["extensions"]["adopted"]["type"] == "book"

    def test_a_registered_type_is_still_the_writer_s_claim(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="declares type 'map'"):
            content.adopt_concept(path("Obsidian/declared.md"), concept_type="knowledge")

    def test_a_registered_type_adopts_with_a_status_set_aside(
        self, content: ContentService
    ) -> None:
        adopted = content.adopt_concept(path("Obsidian/declared.md"))
        assert adopted.document.frontmatter["type"] == "map"
        assert adopted.document.frontmatter["extensions"]["adopted"] == {"status": "wip"}


class TestWhatElseWouldHaveBeenLost:
    def test_a_title_field_replaced_is_kept(self, content: ContentService) -> None:
        adopted = content.adopt_concept(
            path("Obsidian/titled.md"), concept_type="knowledge", fields={"title": "Mine"}
        )
        assert adopted.document.frontmatter["title"] == "Mine"
        assert adopted.document.frontmatter["extensions"]["adopted"] == {
            "title": "The Writer's Title"
        }

    def test_a_title_carried_unchanged_is_not_set_aside(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/titled.md"), concept_type="knowledge")
        assert adopted.document.frontmatter["title"] == "The Writer's Title"
        assert adopted.set_aside == ()

    def test_a_status_field_replaced_is_kept(self, content: ContentService) -> None:
        adopted = content.adopt_concept(
            path("Obsidian/active.md"), concept_type="knowledge", fields={"status": "draft"}
        )
        assert adopted.document.frontmatter["status"] == "draft"
        assert adopted.document.frontmatter["extensions"]["adopted"] == {"status": "active"}

    def test_keys_never4ga_owns_are_kept(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/owned.md"), concept_type="knowledge")
        frontmatter = adopted.document.frontmatter
        assert frontmatter["schema"] == "never4ga/0.1"
        assert frontmatter["extensions"]["adopted"] == {
            "schema": "obsidian/1",
            "generated": "by hand",
        }

    def test_the_writer_s_own_extensions_survive_beside_it(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Obsidian/mine.md"), concept_type="knowledge")
        assert adopted.document.frontmatter["extensions"] == {
            "mine": {"bucket": "weekly"},
            "adopted": {"status": "done"},
        }
        assert adopted.set_aside == ("status",)


class TestStillRefused:
    def test_a_field_the_caller_supplied_that_does_not_fit(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="status must be one of"):
            content.adopt_concept(
                path("Obsidian/plain.md"), concept_type="knowledge", fields={"status": "bogus"}
            )

    def test_an_adopted_namespace_the_writer_already_has(
        self, content: ContentService, root: Path
    ) -> None:
        with pytest.raises(ConceptCreationError, match=r"extensions\.adopted"):
            content.adopt_concept(path("Obsidian/taken.md"), concept_type="knowledge")
        assert (root / "Obsidian/taken.md").is_file()

    def test_extensions_that_are_not_a_mapping(self, content: ContentService, root: Path) -> None:
        with pytest.raises(ConceptCreationError, match="extensions"):
            content.adopt_concept(path("Obsidian/flat.md"), concept_type="knowledge")
        assert (root / "Obsidian/flat.md").is_file()

    def test_a_document_under_a_root_keeps_today_s_refusal(
        self, content: ContentService, root: Path
    ) -> None:
        # It was written for Never4gA, so a value that does not fit is more
        # likely a mistake.
        (root / "30_Knowledge/Notes/hand.md").write_text(
            "---\nstatus: active\n---\n# Hand\n", encoding="utf-8"
        )
        with pytest.raises(ConceptCreationError, match="status must be one of"):
            content.adopt_concept(path("30_Knowledge/Notes/hand.md"), concept_type="knowledge")
