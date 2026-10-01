"""Adopting a note out of foreign material moves it home.

A note in registered foreign material (core/01 section 1, core/02 section 3.3)
has no home to stay in: no registered type is at home in somebody's
`Projects/`, and a binding location is binding. So adoption mints identity,
keeps the body byte for byte, and then moves the file to its type's first
registered location, or to the folder `in_directory` names, reporting both
paths.

The new file keeps its own name, passed through the rule every other concept
path follows. A pile's `index.md` or `log.md` takes its folder's name instead,
since both are reserved in Never4gA's structure. With no type named, a
destination folder decides the type the way adoption in place does; with
neither, the refusal names the types whose home needs no workspace.

A file already inside a root is unchanged by all of this.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.schema import ValidationLevel, validate_document
from never4ga.services import ConceptCreationError, ContentService, VaultInitializer

WORKSPACE_DIRECTORY = "10_Workspaces/Garden"
RAISED_BEDS = "Projects/garden/raised-beds.md"
BODY = "# Raised beds\n\nCedar, not pine, for the zanzibar bays.\n\n- 4 x 8\n- 30 cm deep\n"


def fixed_clock() -> datetime:
    return datetime(2026, 9, 19, 20, 0, 0, tzinfo=UTC)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    # The pile exists before `init`, so `init` registers it.
    write(root, RAISED_BEDS, BODY)
    write(root, "Projects/Seed Catalogue.md", "# Seed catalogue\n\nHeirloom only.\n")
    write(root, "Projects/index.md", "# Projects\n\nEverything I am building.\n")
    write(root, "Projects/garden/log.md", "Planted the beans.\n")
    write(
        root,
        "Reading/fast-and-slow.md",
        "---\ntags: [books]\nrating: 4\n---\n# Fast and slow\n\nSystem one is quick.\n",
    )
    write(root, "Reading/declared.md", "---\ntype: map\n---\n# Declared\n")
    return root


@pytest.fixture
def files(root: Path) -> FileSystemVaultFileStore:
    return FileSystemVaultFileStore(root)


@pytest.fixture
def documents(root: Path) -> FileSystemMarkdownStore:
    return FileSystemMarkdownStore(root)


@pytest.fixture
def content(files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore) -> ContentService:
    VaultInitializer(files, documents, now=fixed_clock).initialize("Test Vault")
    documents.refresh()
    service = ContentService(files, documents, now=fixed_clock, actor="claude-code/test")
    service.create_workspace("Garden", workspace_type="product")
    documents.refresh()
    return service


def path(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


class TestTheMove:
    def test_it_lands_in_its_type_s_home_under_its_own_name(
        self, content: ContentService, root: Path
    ) -> None:
        adopted = content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        assert str(adopted.path) == "30_Knowledge/Notes/raised-beds.md"
        assert (root / "30_Knowledge/Notes/raised-beds.md").is_file()

    def test_the_source_is_gone(self, content: ContentService, root: Path) -> None:
        content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        assert not (root / RAISED_BEDS).exists()

    def test_both_paths_are_reported(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        assert adopted.moved_from == path(RAISED_BEDS)
        assert str(adopted.path) == "30_Knowledge/Notes/raised-beds.md"

    def test_the_reason_says_it_moved_and_why_there(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        assert "foreign material" in adopted.placement_reason
        assert "30_Knowledge/Notes" in adopted.placement_reason
        assert "you named a knowledge" in adopted.placement_reason

    def test_the_body_is_preserved_byte_for_byte(self, content: ContentService, root: Path) -> None:
        adopted = content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        text = (root / str(adopted.path)).read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert text.endswith("---\n" + BODY)

    def test_the_writer_s_own_keys_survive(self, content: ContentService, root: Path) -> None:
        adopted = content.adopt_concept(path("Reading/fast-and-slow.md"), concept_type="knowledge")
        assert adopted.document.frontmatter["tags"] == ["books"]
        assert adopted.document.frontmatter["rating"] == 4
        assert adopted.document.body == "# Fast and slow\n\nSystem one is quick.\n"

    def test_the_frontmatter_is_what_adr_0035_mints(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        frontmatter = adopted.document.frontmatter
        assert frontmatter["type"] == "knowledge"
        assert frontmatter["title"] == "Raised beds"
        assert frontmatter["created_at"] == "2026-09-19T20:00:00Z"
        assert frontmatter["generated"]["by"] == "claude-code/test"
        report = validate_document(adopted.path, frontmatter, level=ValidationLevel.STRICT)
        assert not report.errors

    def test_it_is_a_concept_afterwards(
        self, content: ContentService, documents: FileSystemMarkdownStore
    ) -> None:
        adopted = content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        documents.refresh()
        found = documents.get(adopted.concept_id)
        assert found is not None
        assert found.path == adopted.path

    def test_a_declared_registered_type_needs_no_argument(self, content: ContentService) -> None:
        adopted = content.adopt_concept(path("Reading/declared.md"))
        assert str(adopted.path) == "30_Knowledge/Maps/declared.md"
        assert "the frontmatter declares a map" in adopted.placement_reason


class TestTheName:
    def test_a_name_with_spaces_and_capitals_is_normalised(
        self, content: ContentService, root: Path
    ) -> None:
        adopted = content.adopt_concept(
            path("Projects/Seed Catalogue.md"), concept_type="knowledge"
        )
        assert str(adopted.path) == "30_Knowledge/Notes/seed-catalogue.md"
        assert adopted.document.frontmatter["title"] == "Seed catalogue"

    def test_a_pile_s_index_takes_its_folder_s_name(
        self, content: ContentService, root: Path
    ) -> None:
        adopted = content.adopt_concept(path("Projects/index.md"), concept_type="knowledge")
        assert str(adopted.path) == "30_Knowledge/Notes/projects.md"
        assert not (root / "Projects/index.md").exists()

    def test_a_pile_s_log_takes_its_folder_s_name_and_titles_by_it(
        self, content: ContentService
    ) -> None:
        # No heading, so the name it now has is the only title it ever had.
        adopted = content.adopt_concept(path("Projects/garden/log.md"), concept_type="knowledge")
        assert str(adopted.path) == "30_Knowledge/Notes/garden.md"
        assert adopted.document.frontmatter["title"] == "garden"


class TestTheDestination:
    def test_an_occupied_destination_is_refused_and_nothing_moves(
        self, content: ContentService, root: Path
    ) -> None:
        write(root, "30_Knowledge/Notes/raised-beds.md", "# Someone else's\n")
        with pytest.raises(
            ConceptCreationError, match=re.escape("30_Knowledge/Notes/raised-beds.md")
        ):
            content.adopt_concept(path(RAISED_BEDS), concept_type="knowledge")
        assert (root / RAISED_BEDS).read_text(encoding="utf-8") == BODY
        assert (root / "30_Knowledge/Notes/raised-beds.md").read_text(
            encoding="utf-8"
        ) == "# Someone else's\n"

    def test_a_named_folder_is_where_it_goes(self, content: ContentService, root: Path) -> None:
        adopted = content.adopt_concept(
            path(RAISED_BEDS),
            concept_type="research_note",
            in_directory=path(f"{WORKSPACE_DIRECTORY}/Research"),
        )
        assert str(adopted.path) == f"{WORKSPACE_DIRECTORY}/Research/raised-beds.md"
        assert adopted.document.frontmatter["workspace"]

    def test_a_named_folder_decides_the_type(self, content: ContentService) -> None:
        adopted = content.adopt_concept(
            path(RAISED_BEDS), in_directory=path(f"{WORKSPACE_DIRECTORY}/Decisions")
        )
        assert adopted.document.frontmatter["type"] == "decision"
        assert "the folder says a decision" in adopted.placement_reason

    def test_a_named_folder_several_types_share_names_them(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError) as refused:
            content.adopt_concept(
                path(RAISED_BEDS), in_directory=path(f"{WORKSPACE_DIRECTORY}/Units")
            )
        assert refused.value.candidates == ("course_assignment", "course_unit")

    def test_a_named_folder_the_type_does_not_belong_in_is_refused(
        self, content: ContentService, root: Path
    ) -> None:
        with pytest.raises(ConceptCreationError, match="does not belong in"):
            content.adopt_concept(
                path(RAISED_BEDS),
                concept_type="knowledge",
                in_directory=path(f"{WORKSPACE_DIRECTORY}/Research"),
            )
        assert (root / RAISED_BEDS).is_file()

    def test_a_workspace_type_without_a_folder_asks_for_one(
        self, content: ContentService, root: Path
    ) -> None:
        with pytest.raises(ConceptCreationError, match="name the folder"):
            content.adopt_concept(path(RAISED_BEDS), concept_type="decision")
        assert (root / RAISED_BEDS).is_file()


class TestWithoutAType:
    def test_the_refusal_names_the_types_with_a_home_that_needs_no_workspace(
        self, content: ContentService, root: Path
    ) -> None:
        with pytest.raises(ConceptCreationError) as refused:
            content.adopt_concept(path(RAISED_BEDS))
        assert refused.value.candidates == ("knowledge", "map")
        assert (root / RAISED_BEDS).read_text(encoding="utf-8") == BODY

    def test_the_refusal_says_a_workspace_s_type_needs_a_folder(
        self, content: ContentService
    ) -> None:
        with pytest.raises(ConceptCreationError, match="folder"):
            content.adopt_concept(path(RAISED_BEDS))


class TestInsideARootNothingChanges:
    def test_a_file_under_a_root_stays_where_it_is(
        self, content: ContentService, root: Path
    ) -> None:
        write(root, f"{WORKSPACE_DIRECTORY}/Research/soil.md", "# Soil\n")
        adopted = content.adopt_concept(path(f"{WORKSPACE_DIRECTORY}/Research/soil.md"))
        assert str(adopted.path) == f"{WORKSPACE_DIRECTORY}/Research/soil.md"
        assert adopted.moved_from is None
        assert adopted.placement_reason.startswith("adopted where it sits")

    def test_naming_a_folder_for_a_file_under_a_root_is_refused(
        self, content: ContentService, root: Path
    ) -> None:
        write(root, f"{WORKSPACE_DIRECTORY}/Research/soil.md", "# Soil\n")
        with pytest.raises(ConceptCreationError, match="where it sits"):
            content.adopt_concept(
                path(f"{WORKSPACE_DIRECTORY}/Research/soil.md"),
                in_directory=path("30_Knowledge/Notes"),
            )
        assert (root / f"{WORKSPACE_DIRECTORY}/Research/soil.md").read_text(
            encoding="utf-8"
        ) == "# Soil\n"

    def test_reserved_navigation_under_a_root_is_still_refused(
        self, content: ContentService
    ) -> None:
        with pytest.raises(ConceptCreationError, match="reserved navigation"):
            content.adopt_concept(path(f"{WORKSPACE_DIRECTORY}/index.md"), concept_type="knowledge")

    def test_an_unregistered_top_level_directory_is_not_foreign(
        self, content: ContentService, root: Path
    ) -> None:
        # Added by hand after `init`: `doctor` reports it, and nothing treats
        # it as a pile until it is registered.
        write(root, "Later/thought.md", "# Thought\n")
        with pytest.raises(ConceptCreationError, match="does not belong"):
            content.adopt_concept(path("Later/thought.md"), concept_type="knowledge")
        assert (root / "Later/thought.md").is_file()
