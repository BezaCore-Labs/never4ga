"""The store's view of foreign material (core/01 section 1, core/02 section 3.3).

A directory `init` registered as foreign material holds the user's notes as
they wrote them. The store walks it on the indexer's behalf and hands back
each note that is not a concept -- with no frontmatter, with frontmatter of
its own, or with frontmatter it cannot read -- so the text index can hold it
by path. Nothing here writes: a foreign note is read, never rewritten.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.document_store import FileStatReportingStore, ForeignMaterialStore

pytestmark = pytest.mark.contract

REGISTERED = ("Projects", "Reading")


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


def paths(store: FileSystemMarkdownStore, directories: tuple[str, ...] = REGISTERED) -> list[str]:
    return [str(note.path) for note in store.iter_foreign_notes(directories)]


def test_the_filesystem_store_offers_foreign_material(store: FileSystemMarkdownStore) -> None:
    assert isinstance(store, ForeignMaterialStore)
    assert isinstance(store, FileStatReportingStore)


class TestWhatIsAForeignNote:
    def test_a_note_without_frontmatter_is_one(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/garden/raised-beds.md", "# Raised beds\n\nCedar, not pine.\n")
        (note,) = store.iter_foreign_notes(REGISTERED)
        assert note.path == VaultPath.parse("Projects/garden/raised-beds.md")
        assert note.body == "# Raised beds\n\nCedar, not pine.\n"
        assert note.frontmatter == {}
        assert note.body_offset == 0

    def test_a_note_with_frontmatter_of_its_own_is_one(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Reading/fast-and-slow.md", "---\ntags: [books]\n---\n# Fast and slow\n")
        (note,) = store.iter_foreign_notes(REGISTERED)
        assert note.frontmatter == {"tags": ["books"]}
        assert note.body == "# Fast and slow\n"
        assert note.body_offset == 3

    def test_an_id_that_is_not_ours_does_not_make_a_concept(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # Jekyll and friends use `id` too. Only a Never4gA identity is one.
        write(vault, "Reading/post.md", "---\nid: my-first-post\n---\nHello\n")
        assert paths(store) == ["Reading/post.md"]

    def test_frontmatter_that_cannot_be_read_leaves_the_whole_file_as_text(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # The writer's bytes are still worth finding. Nothing is repaired:
        # the note is read, and `doctor` reports it as untracked.
        write(vault, "Projects/broken.md", "---\ntitle: [unclosed\n---\nCompost ratios\n")
        (note,) = store.iter_foreign_notes(REGISTERED)
        assert note.frontmatter == {}
        assert "Compost ratios" in note.body
        assert note.body_offset == 0

    def test_a_concept_inside_foreign_material_is_not_a_foreign_note(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # It has an identity, so it is indexed as the concept it is.
        write(
            vault,
            "Projects/adopted.md",
            f"---\ntype: knowledge\nid: {ConceptId.new()}\nschema: never4ga/0.1\n"
            'title: Adopted\ncreated_at: "2026-09-17T12:00:00Z"\n---\n# Adopted\n',
        )
        write(vault, "Projects/plain.md", "# Plain\n")
        assert paths(store) == ["Projects/plain.md"]


class TestWhereForeignNotesAre:
    def test_only_registered_directories_are_walked(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/one.md", "# One\n")
        write(vault, "Unregistered/two.md", "# Two\n")
        write(vault, "30_Knowledge/Notes/prose.md", "# Prose\n")
        assert paths(store) == ["Projects/one.md"]

    def test_nested_folders_are_walked_in_a_stable_order(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Reading/z.md", "z\n")
        write(vault, "Projects/b/deep/c.md", "c\n")
        write(vault, "Projects/a.md", "a\n")
        assert paths(store) == ["Projects/a.md", "Projects/b/deep/c.md", "Reading/z.md"]

    def test_dot_directories_and_other_files_are_not_notes(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/.obsidian/workspace.md", "tool state\n")
        write(vault, "Projects/diagram.png", "not markdown")
        write(vault, "Projects/.hidden.md", "hidden\n")
        write(vault, "Projects/real.md", "real\n")
        assert paths(store) == ["Projects/real.md"]

    def test_an_index_or_log_in_a_pile_is_the_writers_note(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # `index.md` and `log.md` are reserved in Never4gA's own structure
        # (core/01 sections 4 and 13). A pile is not that structure: an
        # Obsidian user's `Projects/index.md` is their map of the folder, and
        # skipping it would leave the one note that names everything in it
        # unsearchable.
        write(vault, "Projects/index.md", "---\ntags: [moc]\n---\n# My projects\n")
        write(vault, "Projects/garden/log.md", "# Changelog\n")
        assert paths(store) == ["Projects/garden/log.md", "Projects/index.md"]
        stats = [str(stat.path) for stat in store.iter_foreign_file_stats(REGISTERED)]
        assert stats == ["Projects/garden/log.md", "Projects/index.md"]

    def test_a_registered_directory_that_is_gone_yields_nothing(
        self, store: FileSystemMarkdownStore
    ) -> None:
        assert paths(store) == []

    def test_a_root_is_never_walked_as_foreign_material(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        # A manifest that names a root is corrupt; the roots' prose is
        # `untracked_document`, not a pile to index by path.
        write(vault, "30_Knowledge/Notes/prose.md", "# Prose\n")
        assert paths(store, ("30_Knowledge",)) == []

    def test_a_name_with_a_separator_is_not_a_directory_name(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/a.md", "a\n")
        assert paths(store, ("../outside", "Projects/sub", "")) == []


class TestOneAtATime:
    def test_a_foreign_note_can_be_read_by_path(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/one.md", "# One\n")
        note = store.get_foreign_note(VaultPath.parse("Projects/one.md"))
        assert note is not None
        assert note.body == "# One\n"

    def test_a_missing_file_is_none(self, store: FileSystemMarkdownStore) -> None:
        assert store.get_foreign_note(VaultPath.parse("Projects/gone.md")) is None

    def test_a_concept_is_none(self, store: FileSystemMarkdownStore, vault: Path) -> None:
        write(
            vault,
            "Projects/adopted.md",
            f"---\ntype: knowledge\nid: {ConceptId.new()}\nschema: never4ga/0.1\n"
            'title: Adopted\ncreated_at: "2026-09-17T12:00:00Z"\n---\n# Adopted\n',
        )
        assert store.get_foreign_note(VaultPath.parse("Projects/adopted.md")) is None


class TestContentHash:
    def test_the_same_bytes_hash_the_same(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/one.md", "# One\n")
        first = store.get_foreign_note(VaultPath.parse("Projects/one.md"))
        second = store.get_foreign_note(VaultPath.parse("Projects/one.md"))
        assert first is not None and second is not None
        assert first.content_hash == second.content_hash

    def test_an_edit_changes_the_hash(self, store: FileSystemMarkdownStore, vault: Path) -> None:
        write(vault, "Projects/one.md", "# One\n")
        before = store.get_foreign_note(VaultPath.parse("Projects/one.md"))
        write(vault, "Projects/one.md", "# One, edited\n")
        after = store.get_foreign_note(VaultPath.parse("Projects/one.md"))
        assert before is not None and after is not None
        assert before.content_hash != after.content_hash

    def test_a_move_does_not_change_the_hash(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/one.md", "# One\n")
        write(vault, "Reading/one.md", "# One\n")
        here = store.get_foreign_note(VaultPath.parse("Projects/one.md"))
        there = store.get_foreign_note(VaultPath.parse("Reading/one.md"))
        assert here is not None and there is not None
        assert here.content_hash == there.content_hash


class TestStats:
    def test_every_foreign_note_is_described_without_being_read(
        self, store: FileSystemMarkdownStore, vault: Path
    ) -> None:
        write(vault, "Projects/one.md", "# One\n")
        write(vault, "Reading/two.md", "---\ntags: [x]\n---\nTwo\n")
        write(vault, "Unregistered/three.md", "# Three\n")
        stats = {str(stat.path): stat.size for stat in store.iter_foreign_file_stats(REGISTERED)}
        assert stats == {"Projects/one.md": 6, "Reading/two.md": 22}
