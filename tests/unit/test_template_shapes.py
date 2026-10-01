"""One definition of each concept type's shape, feeding everything.

The templates `init` ships and the bodies the creation verbs write have one
source, `scaffold.TEMPLATES`, so they cannot drift apart. A creation verb takes
its body from the template; a person creating a note by hand in Obsidian gets
the same template. Edit one, both change. Each shape is explained in its
docstring in `scaffold.py`.
"""

from __future__ import annotations

import re

import pytest

from never4ga.schema import TYPE_REGISTRY
from never4ga.schema.types import type_spec
from never4ga.services import dashboard, scaffold
from never4ga.services.scaffold import template_body, template_for

TEMPLATED = sorted(name.removesuffix(".md") for name in scaffold.TEMPLATES)


def headings(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, re.M)


class TestOneSource:
    def test_a_creation_body_is_the_template_body(self) -> None:
        # Same string, not a resemblance.
        assert template_body("workspace", "Never4gA") == scaffold.workspace_body("Never4gA")

    def test_the_template_is_the_adopt_note_plus_that_body(self) -> None:
        # Body-only: a template carries no frontmatter placeholders, and its
        # note says how the file becomes a concept instead.
        text = template_for("decision")
        assert text.startswith("> ")
        assert "never4ga adopt" in text
        assert text.endswith(template_body("decision", "<title>"))

    def test_no_template_opens_a_frontmatter_block(self) -> None:
        # A block without an `id` is a `missing_id` error, so a template that
        # began one would turn every note started from it into a finding.
        for filename, template in sorted(scaffold.TEMPLATES.items()):
            assert not template.startswith("---"), filename

    def test_a_template_is_plain_markdown_and_a_created_file_is_live(self) -> None:
        # core/01 section 5: a shipped file depends on no .base view. Only a
        # live workspace carries the views block. It starts empty, with no
        # views before content, and the refresh every creating verb runs grows
        # it, so the markers are what show a file is live.
        assert dashboard.BEGIN not in template_for("workspace")
        assert dashboard.BEGIN in template_body("workspace", "x")
        assert headings(template_for("workspace")) == headings(template_body("workspace", "x"))

    def test_every_template_names_a_registered_type(self) -> None:
        for name in TEMPLATED:
            assert name.replace("-", "_") in TYPE_REGISTRY, name

    def test_a_template_body_carries_the_title(self) -> None:
        assert template_body("goal", "Ship it").startswith("# Ship it\n")

    def test_an_unknown_type_is_refused(self) -> None:
        with pytest.raises(KeyError):
            template_body("not_a_type", "x")


class TestDecisionShape:
    """The decision shape (core/02 section 21.11)."""

    def test_the_universal_three_are_present(self) -> None:
        h = headings(template_body("decision", "x"))
        assert h[:2] == ["Context", "Decision"]
        assert "Consequences" in h

    def test_consequences_is_plural(self) -> None:
        # A decision rarely has only one consequence.
        assert "Consequence\n" not in template_body("decision", "x")

    def test_alternatives_are_mandatory_under_one_name(self) -> None:
        # One name, not several. A decision without alternatives is an
        # announcement.
        h = headings(template_body("decision", "x"))
        assert "Alternatives" in h
        assert not any(x in h for x in ("Options considered", "Rationale", "Options not taken"))

    def test_revisit_triggers_are_a_section(self) -> None:
        # This is what makes a decision revisitable rather than permanent.
        assert "Revisit when" in headings(template_body("decision", "x"))

    def test_supersession_is_frontmatter_not_prose(self) -> None:
        # core/02 section 21.11: relations: superseded_by. Not a heading.
        assert "Supersession" not in headings(template_body("decision", "x"))


class TestActivityLogShape:
    def test_it_records_what_and_next_and_what_changed_hands(self) -> None:
        h = headings(template_body("activity_log", "x"))
        assert h == ["What happened", "Decided", "Corrected", "Open", "Next"]


class TestPlanShape:
    def test_a_plan_states_its_end_before_its_steps(self) -> None:
        h = headings(template_body("plan", "x"))
        assert h.index("Done when") < h.index("Steps")

    def test_a_plan_says_what_it_leaves_out(self) -> None:
        assert "Not in scope" in headings(template_body("plan", "x"))


class TestWorkspaceShape:
    def test_the_manifest_sections_are_the_template(self) -> None:
        # The view sections (Goals, Decisions, Recent Activity and the rest)
        # are not headings of the shape: `services.dashboard` generates them
        # per kind of content the workspace holds.
        h = headings(template_for("workspace"))
        for section in ("Purpose", "Current State", "External Systems", "Key Links"):
            assert section in h


class TestIntegrationCarriesTheConnectionFields:
    """A connection definition is an `integration` concept.

    The template's note names the four canonical fields to add after adoption,
    so a person does not have to invent key names. It never names a place for
    the token: the obvious place is the one core/03 section 16 forbids.
    """

    def test_the_note_offers_the_canonical_half(self) -> None:
        template = template_for("integration")
        for field in ("connection", "provider", "base_url", "project_ref"):
            assert f"`{field}`" in template

    def test_it_says_where_the_token_goes_instead(self) -> None:
        template = template_for("integration")
        assert "secret" in template.lower()

    def test_it_names_no_key_that_validation_would_refuse(self) -> None:
        from never4ga.domain.connections import FORBIDDEN_CONNECTION_KEYS

        template = template_for("integration")
        for forbidden in FORBIDDEN_CONNECTION_KEYS:
            assert f"`{forbidden}`" not in template


class TestBaseViewsLeadWithTheTitle:
    """A view's first column names the document, and a file name is not its name.

    A file name is a slug; the title says what the document is. A formula
    wrapping `file.asLink` around the title keeps the column clickable, where a
    bare `title` property would render as text. It falls back to a prettified
    file name for a document with no title, such as a foreign file or one
    mid-edit.
    """

    def _views(self) -> list[str]:
        blocks = list(scaffold.BASES.values())
        blocks.extend(template_body(name.replace("-", "_"), "x") for name in TEMPLATED)
        # The dashboard's generated views, rendered with every embed present
        # so the contract covers each one.
        blocks.append(dashboard.block_for({embed.type_name for embed in dashboard.EMBEDS}))
        return blocks

    def test_no_view_leads_with_the_file_name(self) -> None:
        for block in self._views():
            for order in re.findall(r"order:\n((?:\s+- .+\n)+)", block):
                assert order.split("\n")[0].strip() != "- file.name", block

    def test_every_view_leads_with_a_linked_title(self) -> None:
        for block in self._views():
            for order in re.findall(r"order:\n((?:\s+- .+\n)+)", block):
                assert order.split("\n")[0].strip() == "- formula.doc", block

    def test_the_formula_links_the_title_and_falls_back_to_the_file_name(self) -> None:
        for block in self._views():
            if "formula.doc" not in block:
                continue
            assert "file.asLink(" in block, block
            assert "note.title" in block, block
            assert "file.name" in block, block

    def test_a_column_carries_a_readable_name(self) -> None:
        # `formula.doc` is not a column heading a person should have to read.
        for block in self._views():
            if "formula.doc" not in block:
                continue
            assert "displayName:" in block, block


class TestEveryHeadingSaysWhatItIsFor:
    """Every heading carries one line saying what goes under it.

    An empty heading is a prompt, not a standard."""

    def test_no_section_of_any_shape_is_bare(self) -> None:
        for name in TEMPLATED:
            text = template_body(name.replace("-", "_"), "x")
            for heading in headings(text):
                after = text.split(f"## {heading}\n", 1)[1]
                first = after.strip().splitlines()[0] if after.strip() else ""
                assert first and not first.startswith("## "), f"{name}: {heading} is bare"


class TestTheTemplateNamesTheFieldsItsTypeCarries:
    """A template is body-only, so the fields its type carries reach the author
    through the adopt note.

    Lifecycle vocabularies, due dates and a recommended authority are generated
    from the registry, so a vocabulary has one home."""

    def test_every_lifecycle_value_appears_in_its_types_template(self) -> None:
        for name in TEMPLATED:
            spec = type_spec(name.replace("-", "_"))
            assert spec is not None
            text = template_for(name.replace("-", "_"))
            for value in spec.lifecycle_values:
                assert f"`{value}`" in text, f"{name} does not name lifecycle `{value}`"

    def test_a_types_own_optional_fields_appear(self) -> None:
        assert "`due_date`" in template_for("task") and "`priority`" in template_for("task")
        assert "`target_date`" in template_for("goal")
        assert "`opens`" in template_for("course_unit")

    def test_a_recommended_authority_appears(self) -> None:
        # Written rather than recommended: adopt and create both set it, so
        # the template says what an author gets unless they say otherwise.
        assert "`authority` is written as `provisional`" in template_for("research_note")
        assert "`authority` is written as `authoritative`" in template_for("standard")

    def test_the_scope_field_is_not_asked_for(self) -> None:
        # `adopt` writes the scope from where the file sits; asking an author
        # to set it would be asking them to do the verb's one job.
        assert "`workspace`" not in scaffold.fields_note("plan")
        assert "`area`" not in scaffold.fields_note("plan")

    def test_what_the_registry_cannot_say_is_still_said(self) -> None:
        # Type-specific fields core/02 section 21 names in prose rather than
        # as registered extras travel on the shape's own note.
        assert "`aliases`" in template_for("person")
        assert "`resource`" in template_for("resource")
        assert "`stale_after`" in template_for("knowledge")
        assert "relations: implements" in template_for("plan")

    def test_the_note_is_in_the_template_and_not_in_a_created_body(self) -> None:
        # A created document already has its frontmatter written; the note is
        # for the person starting a file by hand.
        assert "one of" in template_for("plan")
        assert "one of" not in template_body("plan", "x")

    def test_it_reads_as_a_sentence(self) -> None:
        note = scaffold.fields_note("task")
        assert note.startswith("This type carries `lifecycle`, one of `todo`, ")
        assert " or `cancelled`" in note
        assert note.endswith("`never4ga concept types task` lists them.")
