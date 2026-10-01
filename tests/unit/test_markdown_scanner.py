"""The Markdown scanner: headings, sections and links.

This is a focused scanner rather than a CommonMark implementation. What
chunking and link extraction need is heading hierarchy, line ranges,
fenced-code awareness and Markdown links, not a full parser.
"""

from __future__ import annotations

import pytest

from never4ga.indexing.markdown import scan_headings, scan_links, scan_sections


class TestHeadings:
    def test_atx_headings_carry_level_text_and_line(self) -> None:
        (heading,) = scan_headings("# Title\n")
        assert (heading.level, heading.text, heading.line) == (1, "Title", 1)

    def test_lines_are_one_based(self) -> None:
        (heading,) = scan_headings("intro\n\n## Later\n")
        assert heading.line == 3

    def test_the_heading_path_is_the_hierarchy(self) -> None:
        source = "# One\n## Two\n### Three\n## Another\n"
        assert [h.path for h in scan_headings(source)] == [
            ("One",),
            ("One", "Two"),
            ("One", "Two", "Three"),
            ("One", "Another"),
        ]

    def test_a_deeper_heading_without_its_parents_still_nests_under_what_precedes(self) -> None:
        # Real documents skip levels. The hierarchy must not lose the ancestor.
        assert [h.path for h in scan_headings("# One\n### Three\n")] == [
            ("One",),
            ("One", "Three"),
        ]

    def test_a_closing_sequence_is_not_part_of_the_text(self) -> None:
        (heading,) = scan_headings("## Title ##\n")
        assert heading.text == "Title"

    @pytest.mark.parametrize(
        "line",
        [
            "#hashtag",  # no space: a tag, not a heading
            "####### Seven",  # only six levels exist
            "    # Indented four spaces",  # an indented code block
            "text # not a heading",
        ],
    )
    def test_what_is_not_a_heading(self, line: str) -> None:
        assert scan_headings(f"{line}\n") == ()

    def test_up_to_three_leading_spaces_is_still_a_heading(self) -> None:
        (heading,) = scan_headings("   # Title\n")
        assert heading.text == "Title"

    def test_an_empty_heading_is_allowed(self) -> None:
        (heading,) = scan_headings("##\n")
        assert (heading.level, heading.text) == (2, "")


class TestSetextHeadings:
    """`Title` underlined with `===` or `---`.

    Read but never written: core/02 section 31, "be liberal in what you can
    read, strict and intentional in what you write". A vault that arrives from
    somewhere else may use them throughout, and a document whose headings are
    invisible chunks as one shapeless section.
    """

    def test_equals_underline_is_a_level_one_heading(self) -> None:
        (heading,) = scan_headings("Title\n=====\n")
        assert (heading.level, heading.text, heading.line) == (1, "Title", 1)

    def test_dash_underline_is_a_level_two_heading(self) -> None:
        (heading,) = scan_headings("Title\n-----\n")
        assert (heading.level, heading.text) == (2, "Title")

    def test_a_single_underline_character_is_enough(self) -> None:
        assert [h.level for h in scan_headings("A\n=\nB\n-\n")] == [1, 2]

    def test_they_nest_with_atx_headings(self) -> None:
        source = "# One\nTwo\n---\n### Three\n"
        assert [h.path for h in scan_headings(source)] == [
            ("One",),
            ("One", "Two"),
            ("One", "Two", "Three"),
        ]

    def test_the_heading_line_is_the_text_not_the_underline(self) -> None:
        (heading,) = scan_headings("intro\n\nTitle\n=====\nbody\n")
        assert heading.line == 3

    def test_a_thematic_break_after_a_blank_line_is_not_a_heading(self) -> None:
        # Nothing to underline: section 31 is about reading generously, not
        # about inventing structure that is not there.
        assert scan_headings("paragraph\n\n---\n") == ()

    def test_a_leading_thematic_break_is_not_a_heading(self) -> None:
        assert scan_headings("---\nbody\n") == ()

    @pytest.mark.parametrize(
        "previous", ["- a list item", "* a list item", "> a quote", "| a | table |", "1. ordered"]
    )
    def test_only_a_paragraph_can_be_underlined(self, previous: str) -> None:
        assert scan_headings(f"{previous}\n---\n") == ()

    def test_an_atx_heading_followed_by_a_rule_stays_one_heading(self) -> None:
        headings = scan_headings("# Title\n---\n")
        assert [(h.level, h.text) for h in headings] == [(1, "Title")]

    def test_an_underline_inside_a_fence_is_not_a_heading(self) -> None:
        assert scan_headings("```\nTitle\n=====\n```\n") == ()

    def test_a_mixed_underline_is_not_a_heading(self) -> None:
        assert scan_headings("Title\n=-=-=\n") == ()

    def test_a_setext_heading_opens_a_section(self) -> None:
        first, second = scan_sections("Title\n=====\nbody\n\nOther\n-----\nmore\n")
        assert first.heading_path == ("Title",)
        assert first.line_range.start == 1
        assert second.heading_path == ("Title", "Other")


class TestFencedCode:
    def test_a_hash_inside_a_fence_is_not_a_heading(self) -> None:
        source = "# Real\n\n```python\n# a comment, not a heading\n```\n\n## Also real\n"
        assert [h.text for h in scan_headings(source)] == ["Real", "Also real"]

    def test_tilde_fences_count(self) -> None:
        assert scan_headings("~~~\n# not a heading\n~~~\n") == ()

    def test_a_fence_is_closed_only_by_its_own_character(self) -> None:
        source = "```\n~~~\n# still inside the fence\n```\n# out\n"
        assert [h.text for h in scan_headings(source)] == ["out"]

    def test_a_longer_closing_fence_closes(self) -> None:
        assert [h.text for h in scan_headings("```\ncode\n`````\n# out\n")] == ["out"]

    def test_a_shorter_fence_does_not_close(self) -> None:
        assert scan_headings("````\n```\n# still inside\n````\n") == ()

    def test_an_info_string_is_allowed(self) -> None:
        assert scan_headings("```python title=x\n# inside\n```\n") == ()

    def test_an_unclosed_fence_runs_to_the_end(self) -> None:
        # Malformed input must not resurrect headings that a reader sees as code.
        assert scan_headings("```\n# inside\n") == ()


class TestSections:
    def test_a_section_spans_from_its_heading_to_the_next(self) -> None:
        source = "# One\nbody\n\n## Two\nmore\n"
        first, second = scan_sections(source)
        assert (first.line_range.start, first.line_range.end) == (1, 2)
        assert (second.line_range.start, second.line_range.end) == (4, 5)

    def test_blank_lines_before_the_next_heading_belong_to_neither_section(self) -> None:
        # A line range is a claim about where the content is. Padding it with
        # the gap makes every excerpt and every citation slightly wrong.
        (section,) = scan_sections("# One\nbody\n\n\n")
        assert section.line_range.end == 2

    def test_content_before_the_first_heading_is_its_own_section(self) -> None:
        preamble, *_ = scan_sections("intro paragraph\n\n# One\nbody\n")
        assert preamble.heading_path == ()
        assert preamble.line_range.start == 1

    def test_a_document_that_opens_with_a_heading_has_no_preamble(self) -> None:
        assert all(section.heading_path for section in scan_sections("# One\nbody\n"))

    def test_a_heading_with_no_body_is_still_a_section(self) -> None:
        (section,) = scan_sections("# Only\n")
        assert section.line_range.start == section.line_range.end == 1

    def test_the_section_text_includes_its_heading(self) -> None:
        (section,) = scan_sections("# Title\nbody\n")
        assert "Title" in section.text
        assert "body" in section.text

    def test_an_empty_document_has_no_sections(self) -> None:
        assert scan_sections("") == ()
        assert scan_sections("\n\n") == ()

    def test_sections_carry_the_heading_hierarchy(self) -> None:
        source = "# One\n## Two\n"
        assert [s.heading_path for s in scan_sections(source)] == [("One",), ("One", "Two")]


class TestWikilinks:
    """`[[Note]]`, which Obsidian writes by default.

    core/02 section 18 prefers standard Markdown links in what Never4gA
    *writes*, and says nothing about what it reads. A vault whose links are all
    invisible gets a `doctor` that reports no broken links, which is a worse
    answer than saying nothing.
    """

    def test_a_bare_wikilink_names_its_target(self) -> None:
        (link,) = scan_links("see [[Other Note]]\n")
        assert link.target == "Other Note"
        assert link.is_wikilink

    def test_a_display_alias_is_not_part_of_the_target(self) -> None:
        (link,) = scan_links("see [[Other Note|the other one]]\n")
        assert link.target == "Other Note"
        assert link.text == "the other one"

    def test_a_heading_anchor_is_separated(self) -> None:
        (link,) = scan_links("see [[Other Note#A Heading]]\n")
        assert (link.target, link.anchor) == ("Other Note", "A Heading")

    def test_a_block_reference_is_kept_as_an_anchor(self) -> None:
        (link,) = scan_links("see [[Other Note#^abc123]]\n")
        assert (link.target, link.anchor) == ("Other Note", "^abc123")

    def test_an_anchor_only_wikilink_addresses_this_document(self) -> None:
        (link,) = scan_links("see [[#A Heading]]\n")
        assert (link.target, link.anchor) == ("", "A Heading")

    def test_a_path_style_wikilink_keeps_its_path(self) -> None:
        (link,) = scan_links("see [[30_Knowledge/Notes/other]]\n")
        assert link.target == "30_Knowledge/Notes/other"

    def test_a_note_embed_is_a_link(self) -> None:
        # Obsidian's graph counts an embedded note as a connection, and a
        # transclusion of a note that does not exist is worth reporting.
        (link,) = scan_links("![[Other Note]]\n")
        assert link.target == "Other Note"

    def test_an_asset_embed_is_not_a_link(self) -> None:
        # Same rule as a Markdown image: assets are not concepts.
        assert scan_links("![[diagram.png]]\n") == ()

    def test_a_wikilink_inside_a_fence_is_not_a_link(self) -> None:
        assert scan_links("```\n[[Other Note]]\n```\n") == ()

    def test_a_wikilink_inside_a_code_span_is_not_a_link(self) -> None:
        assert scan_links("`[[Other Note]]` is how you write one\n") == ()

    def test_a_wikilink_is_never_external(self) -> None:
        (link,) = scan_links("see [[Other Note]]\n")
        assert not link.is_external

    def test_both_link_styles_coexist(self) -> None:
        links = scan_links("see [[Wiki Note]] and [markdown](other.md)\n")
        assert [link.is_wikilink for link in links] == [True, False]

    def test_an_empty_wikilink_is_not_a_link(self) -> None:
        assert scan_links("[[]] and [[   ]]\n") == ()


class TestLinks:
    def test_an_inline_link(self) -> None:
        (link,) = scan_links("see [the note](30_Knowledge/Notes/thing.md)\n")
        assert (link.text, link.target, link.line) == ("the note", "30_Knowledge/Notes/thing.md", 1)

    def test_a_title_is_not_part_of_the_target(self) -> None:
        (link,) = scan_links('[a](b.md "the title")\n')
        assert link.target == "b.md"

    def test_an_anchor_is_separated_from_the_path(self) -> None:
        (link,) = scan_links("[a](b.md#a-heading)\n")
        assert (link.target, link.anchor) == ("b.md", "a-heading")

    def test_a_pure_anchor_link_has_no_target(self) -> None:
        (link,) = scan_links("[a](#a-heading)\n")
        assert (link.target, link.anchor) == ("", "a-heading")

    def test_a_reference_style_link_resolves_through_its_definition(self) -> None:
        (link,) = scan_links("see [the note][ref]\n\n[ref]: 30_Knowledge/Notes/thing.md\n")
        assert link.target == "30_Knowledge/Notes/thing.md"

    def test_a_reference_without_a_definition_is_not_a_link(self) -> None:
        assert scan_links("see [the note][missing]\n") == ()

    def test_a_collapsed_reference_uses_its_own_text_as_the_label(self) -> None:
        (link,) = scan_links("see [ref][]\n\n[ref]: thing.md\n")
        assert link.target == "thing.md"

    def test_an_image_is_not_a_link(self) -> None:
        # Images address assets, not concepts; a broken-link finding about a PNG
        # would be noise (details/data-indexing-maintenance.md section 4).
        assert scan_links("![diagram](Assets/d.png)\n") == ()

    def test_a_markdown_link_is_not_a_wikilink(self) -> None:
        (link,) = scan_links("[a](b.md)\n")
        assert not link.is_wikilink

    def test_an_external_target_is_reported_as_external(self) -> None:
        (link,) = scan_links("[spec](https://example.invalid/x)\n")
        assert link.is_external

    def test_a_vault_relative_target_is_not_external(self) -> None:
        (link,) = scan_links("[a](b.md)\n")
        assert not link.is_external

    def test_a_link_inside_a_fence_is_not_a_link(self) -> None:
        assert scan_links("```\n[a](b.md)\n```\n") == ()

    def test_a_link_inside_an_inline_code_span_is_not_a_link(self) -> None:
        assert scan_links("`[a](b.md)` is how you write one\n") == ()

    def test_several_links_on_one_line(self) -> None:
        assert len(scan_links("[a](a.md) and [b](b.md)\n")) == 2
