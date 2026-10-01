"""The `workspace.md` body is a dashboard, not just a manifest.

details/obsidian-experience.md section 5: "workspace.md is both semantic
manifest and human workspace dashboard", with a suggested structure, and
"Obsidian Bases may be embedded for live vault-derived sections."

The views themselves are `services.dashboard`'s: they live in a managed
block, one per kind of content the workspace actually holds, and the refresh
every creating verb runs keeps them current. What this shape owns is the frame
around them -- the manifest prose headings, and the block's starting position.

The page stays useful with no plugin and no Obsidian at all: every section is
a heading a person can write under, and the block's markers are HTML comments
that render as nothing (core/01 section 5, "The vault remains useful without
the plugin").
"""

from __future__ import annotations

import re

from never4ga.services import dashboard, scaffold

BODY = scaffold.workspace_body("Never4gA")


class TestSuggestedStructure:
    def test_the_manifest_headings_are_present_in_order(self) -> None:
        wanted = [
            "## Purpose",
            "## Current State",
            "## External Systems",
            "## Key Links",
        ]
        positions = [BODY.index(heading) for heading in wanted]
        assert positions == sorted(positions)

    def test_the_title_is_the_h1(self) -> None:
        assert BODY.startswith("# Never4gA\n")


class TestTheViewsBlock:
    def test_the_block_sits_between_current_state_and_external_systems(self) -> None:
        # Where a reader expects the dashboard.
        assert BODY.index("## Current State") < BODY.index(dashboard.BEGIN)
        assert BODY.index(dashboard.END) < BODY.index("## External Systems")

    def test_a_fresh_workspace_starts_with_the_empty_block(self) -> None:
        # No content yet, so no views yet: the block says so, and the refresh
        # a creation runs replaces it the moment content arrives. A view baked
        # in here would never be re-rendered, so a workspace that gained units
        # would have no Units view.
        assert dashboard.block_for(frozenset()) in BODY
        assert "```base" not in BODY

    def test_the_template_carries_no_markers(self) -> None:
        # core/01 section 5: a template is read before anything is live, and
        # the block is machinery a hand-written workspace gains on adoption.
        template = scaffold.template_for("workspace")
        assert dashboard.BEGIN not in template
        assert "```base" not in template

    def test_the_template_says_where_the_views_come_from(self) -> None:
        template = scaffold.template_for("workspace")
        assert "generated" in template

    def test_the_body_holds_no_frontmatter_and_no_identity(self) -> None:
        # The id lives in frontmatter, written by build_concept. A body that
        # hard-coded it would go stale the moment the file was copied as a
        # template.
        assert not BODY.startswith("---")
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-7", BODY)
