"""Deterministic chunks.

core/06 section 4 requires an identity below the document level that is
deterministic from stable source material, so Never4gA can always say which
document, heading, lines, policy and content a retrieved fragment came from.

details/data-indexing-maintenance.md section 6 fixes the policy: document, then
heading sections, then paragraph boundaries when a section is too large. Never a
blind fixed-length window.
"""

from __future__ import annotations

import hashlib
from itertools import pairwise

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.indexing.chunks import CHUNK_POLICY_VERSION, MAX_CHUNK_CHARACTERS, build_chunks


def document(
    body: str, *, concept_id: ConceptId | None = None, body_offset: int = 0
) -> StoredDocument:
    return StoredDocument(
        concept_id=concept_id or ConceptId.new(),
        path=VaultPath.parse("30_Knowledge/Notes/thing.md"),
        frontmatter={"type": "knowledge", "title": "Thing"},
        body=body,
        body_offset=body_offset,
    )


class TestPolicy:
    def test_a_section_becomes_a_chunk(self) -> None:
        chunks = build_chunks(document("# One\nbody\n\n## Two\nmore\n"))
        assert [chunk.chunk.heading_path for chunk in chunks] == [("One",), ("One", "Two")]

    def test_an_empty_document_produces_no_chunks(self) -> None:
        assert build_chunks(document("")) == ()

    def test_a_long_section_splits_at_paragraph_boundaries(self) -> None:
        paragraph = "word " * 200
        body = f"# One\n\n{paragraph}\n\n{paragraph}\n\n{paragraph}\n"
        chunks = build_chunks(document(body))
        assert len(chunks) > 1
        assert all(chunk.text.strip() for chunk in chunks)

    def test_a_single_oversized_paragraph_is_not_cut_in_half(self) -> None:
        # A blind window would split mid-sentence. Section 6 forbids starting there.
        paragraph = "x" * (MAX_CHUNK_CHARACTERS * 2)
        (chunk,) = build_chunks(document(f"# One\n\n{paragraph}\n"))
        assert paragraph in chunk.text

    def test_chunks_do_not_overlap(self) -> None:
        body = "# One\n" + "".join(f"line {n}\n\n" for n in range(200))
        ranges = [chunk.line_range for chunk in build_chunks(document(body))]
        assert all(earlier.end < later.start for earlier, later in pairwise(ranges))

    def test_a_hash_inside_a_fence_does_not_start_a_chunk(self) -> None:
        body = "# One\n\n```python\n# a comment\n```\n"
        assert [chunk.chunk.heading_path for chunk in build_chunks(document(body))] == [("One",)]


class TestIdentity:
    def test_identity_carries_the_five_specified_inputs(self) -> None:
        concept_id = ConceptId.new()
        (chunk,) = build_chunks(document("# One\nbody\n", concept_id=concept_id))
        assert chunk.chunk.concept_id == concept_id
        assert chunk.chunk.heading_path == ("One",)
        assert chunk.chunk.ordinal == 0
        assert chunk.chunk.content_hash == hashlib.sha256(chunk.text.encode()).hexdigest()
        assert chunk.chunk.policy_version == CHUNK_POLICY_VERSION

    def test_the_ordinal_counts_within_its_heading_path(self) -> None:
        # core/06 section 4: "chunk ordinal within locator". Scoping it to the
        # heading is what keeps one section's edit out of another's identity.
        paragraph = "word " * 200
        body = f"# One\n\n{paragraph}\n\n{paragraph}\n\n## Two\n\nshort\n"
        chunks = build_chunks(document(body))
        by_heading: dict[tuple[str, ...], list[int]] = {}
        for chunk in chunks:
            by_heading.setdefault(chunk.chunk.heading_path, []).append(chunk.chunk.ordinal)
        assert by_heading[("One", "Two")] == [0]
        assert by_heading[("One",)] == list(range(len(by_heading[("One",)])))

    def test_identity_is_stable_across_an_unrelated_edit_elsewhere(self) -> None:
        # Editing one section must not re-key the chunks of another, or every
        # unrelated vector and FTS row is invalidated by a typo fix.
        concept_id = ConceptId.new()
        before = build_chunks(document("# One\nbody\n\n## Two\nuntouched\n", concept_id=concept_id))
        after = build_chunks(
            document("# One\nbody, revised\n\n## Two\nuntouched\n", concept_id=concept_id)
        )
        keys = {chunk.chunk.heading_path: chunk.chunk.key for chunk in before}
        assert (
            next(c.chunk.key for c in after if c.chunk.heading_path == ("One", "Two"))
            == (keys[("One", "Two")])
        )
        assert (
            next(c.chunk.key for c in after if c.chunk.heading_path == ("One",)) != (keys[("One",)])
        )

    def test_identity_does_not_depend_on_the_path(self) -> None:
        # core/02 section 5.1: a move changes location, never identity.
        concept_id = ConceptId.new()
        here = build_chunks(document("# One\nbody\n", concept_id=concept_id))
        moved = StoredDocument(
            concept_id=concept_id,
            path=VaultPath.parse("90_Archive/Knowledge/Notes/thing.md"),
            frontmatter={"type": "knowledge", "title": "Thing"},
            body="# One\nbody\n",
        )
        assert here[0].chunk.key == build_chunks(moved)[0].chunk.key

    def test_chunking_is_deterministic(self) -> None:
        body = "# One\nbody\n\n## Two\nmore\n"
        source = document(body)
        assert [c.chunk.key for c in build_chunks(source)] == [
            c.chunk.key for c in build_chunks(source)
        ]


class TestLineNumbersMatchTheFile:
    def test_the_frontmatter_block_is_counted(self) -> None:
        # A result reports source lines. A number that does not match the file
        # someone opens is worse than no number at all.
        (chunk,) = build_chunks(document("# One\nbody\n", body_offset=6))
        assert (chunk.line_range.start, chunk.line_range.end) == (7, 8)

    def test_the_offset_does_not_re_key_a_chunk(self) -> None:
        # Identity is the heading path, the ordinal and the content hash. Adding
        # a frontmatter field must not invalidate every chunk in the document.
        concept_id = ConceptId.new()
        short = build_chunks(document("# One\nbody\n", concept_id=concept_id, body_offset=4))
        long = build_chunks(document("# One\nbody\n", concept_id=concept_id, body_offset=9))
        assert short[0].chunk.key == long[0].chunk.key


class TestProvenance:
    def test_every_chunk_reports_where_it_came_from(self) -> None:
        (chunk,) = build_chunks(document("# One\nbody\n"))
        assert chunk.path == VaultPath.parse("30_Knowledge/Notes/thing.md")
        assert chunk.line_range.start == 1
        assert chunk.line_range.end == 2

    def test_line_ranges_stay_inside_the_document(self) -> None:
        body = "# One\nbody\n\n## Two\nmore\n"
        total = len(body.splitlines())
        assert all(chunk.line_range.end <= total for chunk in build_chunks(document(body)))
