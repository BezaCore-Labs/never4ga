"""A foreign note, cut into chunks the text index can hold by path.

Foreign material is defined in `core/02` §3.3. The same policy that cuts a
concept cuts a foreign note, since a second policy would make the two rank
differently for no reason. The only difference is the owner: a path, never an
identity.
"""

from __future__ import annotations

from never4ga.domain.document import ForeignNote, VaultPath
from never4ga.indexing import project_foreign, written_title
from never4ga.indexing.chunks import CHUNK_POLICY_VERSION
from never4ga.ports.text_index import FIELD_KEYWORDS, FIELD_TITLE

PATH = VaultPath.parse("Projects/garden/raised-beds.md")


def note(
    body: str = "# Raised beds\n\nCedar, not pine.\n",
    *,
    frontmatter: dict[str, object] | None = None,
    body_offset: int = 0,
    path: VaultPath = PATH,
) -> ForeignNote:
    return ForeignNote(path=path, frontmatter=frontmatter or {}, body=body, body_offset=body_offset)


class TestOwnership:
    def test_every_chunk_is_owned_by_the_path(self) -> None:
        chunks = project_foreign(note("# One\n\nA.\n\n## Two\n\nB.\n"))
        assert len(chunks) == 2
        for chunk in chunks:
            assert chunk.chunk.concept_id is None
            assert chunk.chunk.source == PATH
            assert chunk.path == PATH
            assert chunk.chunk.policy_version == CHUNK_POLICY_VERSION

    def test_the_same_note_at_another_path_is_other_chunks(self) -> None:
        here = project_foreign(note())
        there = project_foreign(note(path=VaultPath.parse("Reading/raised-beds.md")))
        assert {c.chunk.key for c in here}.isdisjoint({c.chunk.key for c in there})

    def test_chunks_are_deterministic(self) -> None:
        assert project_foreign(note()) == project_foreign(note())


class TestLines:
    def test_line_ranges_count_from_the_top_of_the_file(self) -> None:
        # Three lines of the writer's own frontmatter precede the body.
        (chunk,) = project_foreign(note("# Heading\n\nText.\n", body_offset=3))
        assert chunk.line_range.start == 4


class TestFields:
    def test_the_title_is_the_first_heading(self) -> None:
        (chunk,) = project_foreign(note())
        assert chunk.metadata[FIELD_TITLE] == "Raised beds"

    def test_a_title_the_writer_declared_wins(self) -> None:
        (chunk,) = project_foreign(note(frontmatter={"title": "Beds for the garden"}))
        assert chunk.metadata[FIELD_TITLE] == "Beds for the garden"

    def test_without_a_heading_the_filename_is_the_title(self) -> None:
        (chunk,) = project_foreign(note("Cedar, not pine.\n"))
        assert chunk.metadata[FIELD_TITLE] == "raised beds"

    def test_the_writers_tags_and_aliases_are_keywords(self) -> None:
        (chunk,) = project_foreign(
            note(frontmatter={"tags": ["garden", "wood"], "aliases": "beds"})
        )
        assert set(chunk.metadata[FIELD_KEYWORDS].split()) == {"garden", "wood", "beds"}


class TestWrittenTitle:
    """The one rule for what a hand-written file is called.

    `adopt` and the indexer both need it, and a second copy would drift.
    """

    def test_the_first_level_one_heading(self) -> None:
        assert written_title(PATH, "Intro\n\n# Real title\n\n## Sub\n") == "Real title"

    def test_otherwise_the_filename_read_through_adr_0008(self) -> None:
        assert written_title(PATH, "## Only a subheading\n") == "raised beds"


def test_an_empty_note_has_no_chunks() -> None:
    assert project_foreign(note("")) == ()
