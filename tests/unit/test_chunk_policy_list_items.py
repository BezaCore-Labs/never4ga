"""chunk/0.2: an oversized block is cut at list-item boundaries.

Lexical search does not need this: BM25 over a long chunk still matches its
terms. Vector search does, because one huge chunk becomes one vector averaging
many unrelated items.

A long prose paragraph is still left whole. Cutting one would put a boundary
mid-sentence. A list item begins a real boundary; a sentence does not.
"""

from __future__ import annotations

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.indexing.chunks import CHUNK_POLICY_VERSION, MAX_CHUNK_CHARACTERS, build_chunks


def document(body: str) -> StoredDocument:
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse("30_Knowledge/Notes/thing.md"),
        frontmatter={"type": "knowledge", "title": "Thing"},
        body=body,
    )


def long_list(items: int, width: int = 300, bullet: str = "-") -> str:
    """A list with no blank line between items: one paragraph, however long."""
    return "\n".join(f"{bullet} {'word ' * (width // 5)}".rstrip() for _ in range(items))


class TestTheVersionChanged:
    def test_it_is_chunk_0_2(self) -> None:
        """Identity must move with the policy, or an old key means something new."""
        assert CHUNK_POLICY_VERSION == "chunk/0.2"


class TestAnOversizedListIsCut:
    def test_a_long_bulleted_block_becomes_several_chunks(self) -> None:
        chunks = build_chunks(document(f"# One\n\n{long_list(40)}\n"))
        assert len(chunks) > 1

    def test_no_chunk_of_a_uniform_list_is_wildly_oversized(self) -> None:
        chunks = build_chunks(document(f"# One\n\n{long_list(40)}\n"))
        # One item may overshoot the budget; a whole list must not.
        assert max(len(chunk.text) for chunk in chunks) < MAX_CHUNK_CHARACTERS * 2

    def test_every_cut_lands_on_a_list_item(self) -> None:
        chunks = build_chunks(document(f"# One\n\n{long_list(40)}\n"))
        for chunk in chunks[1:]:
            assert chunk.text.lstrip().startswith("- ")

    def test_numbered_lists_are_cut_the_same_way(self) -> None:
        body = "\n".join(f"{n}. {'word ' * 60}".rstrip() for n in range(1, 41))
        chunks = build_chunks(document(f"# One\n\n{body}\n"))
        assert len(chunks) > 1

    def test_nothing_is_lost(self) -> None:
        """A chunker that drops text is worse than one that returns big chunks."""
        items = long_list(40)
        chunks = build_chunks(document(f"# One\n\n{items}\n"))
        rejoined = "\n".join(chunk.text for chunk in chunks)
        for line in items.splitlines():
            assert line in rejoined


class TestProseIsStillLeftAlone:
    def test_a_long_paragraph_is_not_cut(self) -> None:
        """Prose is never cut, so no boundary lands mid-sentence."""
        paragraph = "word " * 800
        chunks = build_chunks(document(f"# One\n\n{paragraph.strip()}\n"))
        assert len(chunks) == 1
        assert len(chunks[0].text) > MAX_CHUNK_CHARACTERS

    def test_a_short_list_is_not_cut(self) -> None:
        chunks = build_chunks(document(f"# One\n\n{long_list(3)}\n"))
        assert len(chunks) == 1

    def test_a_list_of_short_items_stays_one_chunk_until_it_is_too_long(self) -> None:
        small = build_chunks(document("# One\n\n" + "\n".join("- item" for _ in range(20))))
        assert len(small) == 1
