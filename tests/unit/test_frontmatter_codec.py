"""Markdown + YAML frontmatter codec.

Specification:

- core/02 section 5.1 -- concepts are YAML frontmatter plus a Markdown body.
- core/02 section 7.4 -- timestamps carry an explicit offset and writers quote
  them; a reader must not retype them on the way past.
- core/02 section 31 -- permissive reader: unknown keys, types, profiles and
  namespaced extensions are preserved.

The codec is the only place YAML is understood.
"""

from __future__ import annotations

import pytest

from never4ga.adapters.filesystem.frontmatter import (
    FrontmatterError,
    parse_document,
    render_document,
)


class TestSplitting:
    def test_frontmatter_and_body_are_separated(self) -> None:
        parsed = parse_document("---\ntitle: Thing\n---\n# Heading\n\nText.\n")
        assert parsed.frontmatter == {"title": "Thing"}
        assert parsed.body == "# Heading\n\nText.\n"

    def test_a_document_without_frontmatter_is_all_body(self) -> None:
        parsed = parse_document("# Heading\n\nText.\n")
        assert parsed.frontmatter is None
        assert parsed.body == "# Heading\n\nText.\n"

    def test_an_empty_frontmatter_block_is_an_empty_mapping(self) -> None:
        parsed = parse_document("---\n---\nbody\n")
        assert parsed.frontmatter == {}
        assert parsed.body == "body\n"

    def test_a_leading_horizontal_rule_is_not_frontmatter(self) -> None:
        # No closing delimiter, so there is no frontmatter block to speak of.
        text = "---\n\nJust a rule and some prose.\n"
        parsed = parse_document(text)
        assert parsed.frontmatter is None
        assert parsed.body == text

    def test_a_rule_inside_the_body_is_left_alone(self) -> None:
        parsed = parse_document("---\ntitle: Thing\n---\nabove\n\n---\n\nbelow\n")
        assert parsed.frontmatter == {"title": "Thing"}
        assert parsed.body == "above\n\n---\n\nbelow\n"

    def test_an_empty_document_has_no_frontmatter(self) -> None:
        parsed = parse_document("")
        assert parsed.frontmatter is None
        assert parsed.body == ""

    def test_a_document_that_is_only_frontmatter_has_an_empty_body(self) -> None:
        parsed = parse_document("---\ntitle: Thing\n---\n")
        assert parsed.frontmatter == {"title": "Thing"}
        assert parsed.body == ""

    def test_frontmatter_must_start_on_the_first_line(self) -> None:
        text = "\n---\ntitle: Thing\n---\n"
        parsed = parse_document(text)
        assert parsed.frontmatter is None
        assert parsed.body == text

    def test_a_non_mapping_frontmatter_block_is_an_error(self) -> None:
        with pytest.raises(FrontmatterError, match="mapping"):
            parse_document("---\n- one\n- two\n---\nbody\n")

    def test_malformed_yaml_raises_a_never4ga_error(self) -> None:
        with pytest.raises(FrontmatterError):
            parse_document("---\ntitle: [unclosed\n---\nbody\n")


class TestScalarFidelity:
    """core/02 section 7.4: nothing may be silently retyped."""

    def test_an_unquoted_date_stays_a_string(self) -> None:
        parsed = parse_document("---\ndue_date: 2026-08-22\n---\n")
        assert parsed.frontmatter is not None
        assert parsed.frontmatter["due_date"] == "2026-08-22"

    def test_an_unquoted_timestamp_stays_a_string(self) -> None:
        parsed = parse_document("---\ncreated_at: 2026-08-22T19:00:00Z\n---\n")
        assert parsed.frontmatter is not None
        assert parsed.frontmatter["created_at"] == "2026-08-22T19:00:00Z"

    def test_a_quoted_timestamp_stays_a_string(self) -> None:
        parsed = parse_document('---\ncreated_at: "2026-08-22T19:00:00Z"\n---\n')
        assert parsed.frontmatter is not None
        assert parsed.frontmatter["created_at"] == "2026-08-22T19:00:00Z"

    @pytest.mark.parametrize("word", ["no", "yes", "on", "off", "y", "n"])
    def test_yaml_11_boolean_words_stay_strings(self, word: str) -> None:
        # YAML 1.1 would coerce these. A vault holding `country: no` (Norway) or
        # `answer: n` must not have it rewritten as `false`.
        parsed = parse_document(f"---\nvalue: {word}\n---\n")
        assert parsed.frontmatter is not None
        assert parsed.frontmatter["value"] == word

    def test_real_booleans_are_still_booleans(self) -> None:
        parsed = parse_document("---\nenabled: true\ndisabled: false\n---\n")
        assert parsed.frontmatter is not None
        assert parsed.frontmatter["enabled"] is True
        assert parsed.frontmatter["disabled"] is False

    def test_numbers_and_nulls_parse_normally(self) -> None:
        parsed = parse_document("---\ncount: 3\nratio: 1.5\nnothing: null\n---\n")
        assert parsed.frontmatter is not None
        assert parsed.frontmatter["count"] == 3
        assert parsed.frontmatter["ratio"] == 1.5
        assert parsed.frontmatter["nothing"] is None


class TestRoundTrip:
    """core/02 section 31: an unmodified document renders back byte for byte."""

    def test_rendering_an_unmodified_document_reproduces_it_exactly(self) -> None:
        text = (
            "---\n"
            "type: knowledge\n"
            "id: 0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31\n"
            "schema: never4ga/0.1\n"
            "title: Hybrid Retrieval\n"
            'created_at: "2026-08-22T19:00:00Z"\n'
            "domains:\n"
            "  - artificial_intelligence\n"
            "---\n"
            "# Hybrid Retrieval\n"
            "\n"
            "Body text.\n"
        )
        parsed = parse_document(text)
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_comments_survive_the_round_trip(self) -> None:
        text = (
            "---\n"
            "# the user's own note about this file\n"
            "type: knowledge\n"
            "title: Thing  # inline note\n"
            "---\n"
            "body\n"
        )
        parsed = parse_document(text)
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_key_order_survives_the_round_trip(self) -> None:
        text = "---\nzebra: 1\nalpha: 2\nmiddle: 3\n---\nbody\n"
        parsed = parse_document(text)
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_blank_lines_inside_frontmatter_survive(self) -> None:
        text = "---\ntype: knowledge\n\nstatus: stable\n---\nbody\n"
        parsed = parse_document(text)
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_unknown_nested_extension_data_survives(self) -> None:
        text = (
            "---\n"
            "type: knowledge\n"
            "extensions:\n"
            "  my_local_workflow:\n"
            "    review_bucket: weekly\n"
            "    depth: 3\n"
            "unknown_future_field:\n"
            "  - a\n"
            "  - b\n"
            "---\n"
            "body\n"
        )
        parsed = parse_document(text)
        assert parsed.frontmatter is not None
        assert parsed.frontmatter["extensions"]["my_local_workflow"]["review_bucket"] == "weekly"
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_quoting_style_is_not_normalised(self) -> None:
        text = "---\nsingle: 'one'\ndouble: \"two\"\nbare: three\n---\nbody\n"
        parsed = parse_document(text)
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_a_body_without_a_trailing_newline_stays_that_way(self) -> None:
        text = "---\ntype: knowledge\n---\nno trailing newline"
        parsed = parse_document(text)
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_a_bodyless_document_round_trips(self) -> None:
        text = "---\ntype: knowledge\n---\n"
        parsed = parse_document(text)
        assert render_document(parsed.frontmatter, parsed.body) == text

    def test_crlf_line_endings_are_preserved(self) -> None:
        text = "---\r\ntype: knowledge\r\ntitle: Thing\r\n---\r\nbody\r\n"
        parsed = parse_document(text)
        assert parsed.frontmatter == {"type": "knowledge", "title": "Thing"}
        assert render_document(parsed.frontmatter, parsed.body, line_ending="\r\n") == text

    def test_the_parsed_line_ending_is_reported(self) -> None:
        assert parse_document("---\r\na: 1\r\n---\r\nbody\r\n").line_ending == "\r\n"
        assert parse_document("---\na: 1\n---\nbody\n").line_ending == "\n"


class TestRendering:
    def test_a_plain_mapping_renders_as_frontmatter(self) -> None:
        rendered = render_document({"type": "knowledge", "title": "Thing"}, "body\n")
        assert rendered == "---\ntype: knowledge\ntitle: Thing\n---\nbody\n"

    def test_none_frontmatter_renders_the_body_alone(self) -> None:
        assert render_document(None, "# Just Markdown\n") == "# Just Markdown\n"

    def test_an_empty_mapping_still_renders_a_block(self) -> None:
        # An empty block is meaningful: it says "this file has frontmatter, and it
        # is empty", which differs from a file that has none.
        assert render_document({}, "body\n") == "---\n---\nbody\n"

    def test_timestamps_are_quoted_when_written_fresh(self) -> None:
        # core/02 section 7.4: "Writers SHOULD quote timestamps in YAML to avoid
        # parser-dependent implicit typing."
        rendered = render_document({"created_at": "2026-08-22T19:00:00Z"}, "")
        assert 'created_at: "2026-08-22T19:00:00Z"' in rendered

    def test_dates_are_quoted_when_written_fresh(self) -> None:
        rendered = render_document({"due_date": "2026-08-22"}, "")
        assert 'due_date: "2026-08-22"' in rendered

    def test_ambiguous_boolean_words_are_quoted_when_written_fresh(self) -> None:
        # Written bare, a YAML 1.1 reader elsewhere would turn this into False.
        rendered = render_document({"value": "no"}, "")
        assert 'value: "no"' in rendered

    def test_ordinary_strings_are_not_gratuitously_quoted(self) -> None:
        rendered = render_document({"title": "Hybrid Retrieval"}, "")
        assert "title: Hybrid Retrieval\n" in rendered

    def test_nested_structures_render(self) -> None:
        rendered = render_document(
            {"generated": {"by": "human:owner", "at": "2026-08-22T19:00:00Z"}}, ""
        )
        assert rendered == (
            '---\ngenerated:\n  by: human:owner\n  at: "2026-08-22T19:00:00Z"\n---\n'
        )

    def test_lists_render_as_block_sequences(self) -> None:
        rendered = render_document({"domains": ["a", "b"]}, "")
        assert rendered == "---\ndomains:\n  - a\n  - b\n---\n"

    def test_an_empty_list_renders_inline(self) -> None:
        assert render_document({"tags": []}, "") == "---\ntags: []\n---\n"

    def test_reparsing_rendered_output_yields_the_same_data(self) -> None:
        data = {
            "type": "knowledge",
            "title": "Thing",
            "created_at": "2026-08-22T19:00:00Z",
            "domains": ["artificial_intelligence"],
            "extensions": {"ns": {"k": "v"}},
        }
        reparsed = parse_document(render_document(data, "body\n"))
        assert reparsed.frontmatter == data
        assert reparsed.body == "body\n"
