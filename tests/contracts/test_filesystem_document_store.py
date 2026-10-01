"""FileSystemMarkdownStore -- the v0.1 DocumentStore (core/05 section 21).

The reusable contract suite runs against it unchanged (core/06 section 22); the
rest of this module covers what only a real filesystem can exercise: bytes on
disk, moves, duplicate identities and files Never4gA does not own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.errors import DuplicateConceptIdError, VaultPathError
from tests.contracts.document_store_contract import DocumentStoreContract, make_document

pytestmark = pytest.mark.contract


class TestFileSystemMarkdownStore(DocumentStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> FileSystemMarkdownStore:
        return FileSystemMarkdownStore(tmp_path)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def store(vault: Path) -> FileSystemMarkdownStore:
    return FileSystemMarkdownStore(vault)


def write(vault: Path, relative: str, text: str) -> Path:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


CONCEPT = """---
type: knowledge
id: {id}
schema: never4ga/0.1
title: {title}
created_at: "2026-08-22T19:00:00Z"
---
# {title}
"""


class TestOnDiskRepresentation:
    def test_a_stored_document_becomes_a_markdown_file(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        document = make_document(path="30_Knowledge/Notes/Thing.md")
        store.put(document)

        written = vault / "30_Knowledge" / "Notes" / "Thing.md"
        assert written.is_file()
        text = written.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert f"id: {document.concept_id}\n" in text
        assert text.endswith("# Thing\n")

    def test_intermediate_directories_are_created(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        store.put(make_document(path="10_Workspaces/A/Workspaces/B/Goals/g.md"))
        assert (vault / "10_Workspaces/A/Workspaces/B/Goals/g.md").is_file()

    def test_files_are_written_as_utf8(self, store: FileSystemMarkdownStore, vault: Path) -> None:
        store.put(make_document(path="30_Knowledge/Notes/Ålesund.md", body="Fjørd — naïve\n"))
        written = vault / "30_Knowledge/Notes/Ålesund.md"
        assert "Fjørd — naïve" in written.read_text(encoding="utf-8")

    def test_no_stray_temporary_file_is_left_behind(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        store.put(make_document())
        assert [p.name for p in (vault / "30_Knowledge/Notes").iterdir()] == ["thing.md"]

    def test_an_existing_file_is_read_without_having_been_written_here(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        concept_id = ConceptId.new()
        write(vault, "30_Knowledge/Notes/Hand Written.md", CONCEPT.format(id=concept_id, title="X"))

        loaded = store.get(concept_id)
        assert loaded is not None
        assert str(loaded.path) == "30_Knowledge/Notes/Hand Written.md"
        assert loaded.body == "# X\n"


class TestIdentityAcrossMoves:
    """A concept's UUID stays stable across rename and move."""

    def test_a_rename_keeps_the_identity(self, store: FileSystemMarkdownStore, vault: Path) -> None:
        original = make_document(path="30_Knowledge/Notes/Old Name.md")
        store.put(original)

        renamed = StoredDocument(
            concept_id=original.concept_id,
            path=VaultPath.parse("30_Knowledge/Notes/New Name.md"),
            frontmatter=original.frontmatter,
            body=original.body,
        )
        store.put(renamed)

        assert not (vault / "30_Knowledge/Notes/Old Name.md").exists()
        assert (vault / "30_Knowledge/Notes/New Name.md").is_file()
        found = store.get(original.concept_id)
        assert found is not None
        assert str(found.path) == "30_Knowledge/Notes/New Name.md"

    def test_a_move_between_roots_keeps_the_identity(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        original = make_document(path="30_Knowledge/Notes/Thing.md")
        store.put(original)
        moved = StoredDocument(
            concept_id=original.concept_id,
            path=VaultPath.parse("90_Archive/Knowledge/Notes/Thing.md"),
            frontmatter=original.frontmatter,
            body=original.body,
        )
        store.put(moved)

        assert store.get(original.concept_id) is not None
        assert not (vault / "30_Knowledge/Notes/Thing.md").exists()

    def test_a_file_moved_behind_never4gas_back_is_still_found_by_id(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # Obsidian and the user move files too. Identity lives in frontmatter,
        # not in any Never4gA-owned mapping (core/02 section 5.1).
        concept_id = ConceptId.new()
        write(vault, "30_Knowledge/Notes/Before.md", CONCEPT.format(id=concept_id, title="T"))
        assert store.get(concept_id) is not None

        (vault / "00_Inbox").mkdir(exist_ok=True)
        (vault / "30_Knowledge/Notes/Before.md").rename(vault / "00_Inbox/After.md")

        store.refresh()
        found = store.get(concept_id)
        assert found is not None
        assert str(found.path) == "00_Inbox/After.md"


class TestDuplicateIdentities:
    """A duplicated UUID is detected and reported."""

    def test_duplicates_are_reported_rather_than_silently_resolved(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        concept_id = ConceptId.new()
        write(vault, "30_Knowledge/Notes/A.md", CONCEPT.format(id=concept_id, title="A"))
        write(vault, "30_Knowledge/Notes/B.md", CONCEPT.format(id=concept_id, title="B"))

        duplicates = store.duplicate_ids()
        assert concept_id in duplicates
        assert {str(path) for path in duplicates[concept_id]} == {
            "30_Knowledge/Notes/A.md",
            "30_Knowledge/Notes/B.md",
        }

    def test_getting_a_duplicated_identity_refuses_to_guess(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        concept_id = ConceptId.new()
        write(vault, "30_Knowledge/Notes/A.md", CONCEPT.format(id=concept_id, title="A"))
        write(vault, "30_Knowledge/Notes/B.md", CONCEPT.format(id=concept_id, title="B"))

        with pytest.raises(DuplicateConceptIdError, match=str(concept_id)):
            store.get(concept_id)

    def test_one_duplicate_does_not_break_the_rest_of_the_vault(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # doctor must be able to run on a vault that has a problem in it.
        clashing = ConceptId.new()
        healthy = ConceptId.new()
        write(vault, "30_Knowledge/Notes/A.md", CONCEPT.format(id=clashing, title="A"))
        write(vault, "30_Knowledge/Notes/B.md", CONCEPT.format(id=clashing, title="B"))
        write(vault, "30_Knowledge/Notes/C.md", CONCEPT.format(id=healthy, title="C"))

        assert store.get(healthy) is not None
        assert len(list(store.iter_documents())) == 3

    def test_a_path_can_still_be_read_when_its_id_is_duplicated(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        concept_id = ConceptId.new()
        write(vault, "30_Knowledge/Notes/A.md", CONCEPT.format(id=concept_id, title="A"))
        write(vault, "30_Knowledge/Notes/B.md", CONCEPT.format(id=concept_id, title="B"))

        loaded = store.get_by_path(VaultPath.parse("30_Knowledge/Notes/A.md"))
        assert loaded is not None
        assert loaded.frontmatter["title"] == "A"


class TestNonConceptFiles:
    def test_reserved_navigation_files_are_not_concepts(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # core/02 section 3.2: index.md and log.md are not concept documents.
        write(vault, "index.md", '---\nokf_version: "0.2"\n---\n# Vault\n')
        write(vault, "30_Knowledge/index.md", "# Knowledge\n")
        write(vault, "10_Workspaces/A/log.md", "# History\n")
        write(vault, "30_Knowledge/Notes/Real.md", CONCEPT.format(id=ConceptId.new(), title="R"))

        assert [str(d.path) for d in store.iter_documents()] == ["30_Knowledge/Notes/Real.md"]

    def test_a_markdown_file_with_no_frontmatter_is_not_a_concept(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "30_Knowledge/Notes/Plain.md", "Just prose.\n")
        assert list(store.iter_documents()) == []
        assert store.get_by_path(VaultPath.parse("30_Knowledge/Notes/Plain.md")) is None

    def test_non_markdown_files_are_ignored(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "30_Knowledge/Assets/diagram.svg", "<svg/>")
        write(vault, "50_System/Integrations/Obsidian/Bases/all.base", "{}")
        assert list(store.iter_documents()) == []

    def test_dot_directories_are_skipped(self, store: FileSystemMarkdownStore, vault: Path) -> None:
        # .obsidian, .git and friends are not vault content.
        write(vault, ".obsidian/plugins/notes.md", CONCEPT.format(id=ConceptId.new(), title="X"))
        write(vault, ".git/COMMIT_EDITMSG.md", CONCEPT.format(id=ConceptId.new(), title="Y"))
        assert list(store.iter_documents()) == []

    def test_a_concept_with_no_id_is_reported_as_a_problem(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "30_Knowledge/Notes/NoId.md", "---\ntype: knowledge\ntitle: X\n---\nbody\n")
        problems = store.problems()
        assert [str(problem.path) for problem in problems] == ["30_Knowledge/Notes/NoId.md"]
        assert problems[0].code == "missing_id"

    def test_a_concept_with_an_unparseable_id_is_reported(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "30_Knowledge/Notes/BadId.md", "---\ntype: knowledge\nid: 42\n---\nbody\n")
        assert [problem.code for problem in store.problems()] == ["invalid_id"]

    def test_unreadable_yaml_is_reported_rather_than_raised(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "30_Knowledge/Notes/Broken.md", "---\ntitle: [unclosed\n---\nbody\n")
        assert [problem.code for problem in store.problems()] == ["unreadable_frontmatter"]
        assert list(store.iter_documents()) == []


class TestPreservationOnUpdate:
    def test_unknown_fields_survive_a_read_modify_write_cycle(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        concept_id = ConceptId.new()
        write(
            vault,
            "30_Knowledge/Notes/Thing.md",
            f"---\n"
            f"type: knowledge\n"
            f"id: {concept_id}\n"
            f"schema: never4ga/0.1\n"
            f"title: Thing\n"
            f'created_at: "2026-08-22T19:00:00Z"\n'
            f"extensions:\n"
            f"  my_workflow:\n"
            f"    bucket: weekly\n"
            f"future_field: keep me\n"
            f"---\n"
            f"body\n",
        )

        loaded = store.get(concept_id)
        assert loaded is not None
        updated = StoredDocument(
            concept_id=concept_id,
            path=loaded.path,
            frontmatter={**loaded.frontmatter, "title": "Renamed"},
            body=loaded.body,
        )
        store.put(updated)

        text = (vault / "30_Knowledge/Notes/Thing.md").read_text(encoding="utf-8")
        assert "future_field: keep me" in text
        assert "bucket: weekly" in text
        assert "title: Renamed" in text

    def test_frontmatter_comments_survive_an_update(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # Never4gA is not the only writer of these files.
        concept_id = ConceptId.new()
        write(
            vault,
            "30_Knowledge/Notes/Thing.md",
            f"---\n"
            f"# my own note to self\n"
            f"type: knowledge\n"
            f"id: {concept_id}\n"
            f"schema: never4ga/0.1\n"
            f"title: Thing\n"
            f'created_at: "2026-08-22T19:00:00Z"\n'
            f"---\n"
            f"body\n",
        )

        loaded = store.get(concept_id)
        assert loaded is not None
        store.put(
            StoredDocument(
                concept_id=concept_id,
                path=loaded.path,
                frontmatter={**loaded.frontmatter, "status": "stable"},
                body=loaded.body,
            )
        )

        text = (vault / "30_Knowledge/Notes/Thing.md").read_text(encoding="utf-8")
        assert "# my own note to self" in text
        assert "status: stable" in text

    def test_a_removed_field_is_actually_removed(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        document = make_document(status="draft")
        store.put(document)
        frontmatter = {k: v for k, v in document.frontmatter.items() if k != "status"}
        store.put(
            StoredDocument(
                concept_id=document.concept_id,
                path=document.path,
                frontmatter=frontmatter,
                body=document.body,
            )
        )
        text = (vault / "30_Knowledge/Notes/thing.md").read_text(encoding="utf-8")
        assert "status:" not in text

    def test_rewriting_an_unchanged_document_leaves_the_bytes_alone(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        concept_id = ConceptId.new()
        original = CONCEPT.format(id=concept_id, title="Thing")
        path = write(vault, "30_Knowledge/Notes/Thing.md", original)

        loaded = store.get(concept_id)
        assert loaded is not None
        store.put(loaded)
        assert path.read_text(encoding="utf-8") == original


class TestVaultBoundary:
    def test_the_root_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(VaultPathError, match="does not exist"):
            FileSystemMarkdownStore(tmp_path / "nope")

    def test_the_root_must_be_a_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "file.md"
        target.write_text("x")
        with pytest.raises(VaultPathError, match="directory"):
            FileSystemMarkdownStore(target)

    def test_a_symlink_pointing_outside_the_vault_is_not_followed(
        self, store: FileSystemMarkdownStore, vault: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        (outside / "secret.md").write_text(CONCEPT.format(id=ConceptId.new(), title="Secret"))
        (vault / "30_Knowledge").mkdir(parents=True)
        (vault / "30_Knowledge/Notes").symlink_to(outside, target_is_directory=True)

        assert list(store.iter_documents()) == []
