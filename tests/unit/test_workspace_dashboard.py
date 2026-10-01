"""Generated workspace dashboard views: one per kind of content actually held.

A fixed set of views for every workspace would give a course workspace no
Units view and a workspace with no goals a Goals view over nothing, and leave
most documents reachable only through the folder tree. So a workspace gets one
view per type it actually holds.

The views live in a managed block inside `workspace.md`, exactly as navigation
lives in `index.md`: markers own the region, prose outside them is never
touched, and the content is derived from the corpus so it cannot drift without
`doctor` seeing it.
"""

from __future__ import annotations

from typing import Any

from never4ga.adapters.fakes import InMemoryVaultFileStore
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.schema import TYPE_REGISTRY, LocationKind
from never4ga.services import dashboard
from never4ga.services.dashboard import (
    BEGIN,
    END,
    block_for,
    current_block,
    matches,
    presence_of,
    refresh,
    spliced,
)


def doc(path: str, concept_type: str, **frontmatter: Any) -> StoredDocument:
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(path),
        frontmatter={"type": concept_type, "schema": "never4ga/0.1", **frontmatter},
        body="",
    )


def workspace(directory: str) -> StoredDocument:
    return doc(f"{directory}/workspace.md", "workspace")


W = "10_Workspaces/Example"


class TestPresence:
    def test_a_concept_counts_toward_its_own_workspace(self) -> None:
        present = presence_of([workspace(W), doc(f"{W}/Goals/ship.md", "goal")])
        assert "goal" in present[VaultPath.parse(W)]

    def test_a_child_concept_never_counts_toward_the_parent(self) -> None:
        # A child course's units are its own; a parent showing a Units view
        # over its children's coursework would be reading over shoulders.
        child = f"{W}/Workspaces/Child"
        present = presence_of(
            [workspace(W), workspace(child), doc(f"{child}/Units/unit-1.md", "course_unit")]
        )
        assert "course_unit" not in present[VaultPath.parse(W)]
        assert "course_unit" in present[VaultPath.parse(child)]

    def test_a_child_manifest_marks_the_parent_as_having_children(self) -> None:
        child = f"{W}/Workspaces/Child"
        present = presence_of([workspace(W), workspace(child)])
        assert "workspace" in present[VaultPath.parse(W)]

    def test_a_workspace_manifest_is_not_its_own_child(self) -> None:
        present = presence_of([workspace(W)])
        assert "workspace" not in present[VaultPath.parse(W)]


class TestWhatTheBlockCarries:
    def test_a_type_the_workspace_holds_gets_a_view(self) -> None:
        block = block_for({"goal"})
        assert "## Goals" in block
        assert '- type == "goal"' in block
        assert "- workspace == this.id" in block

    def test_a_type_the_workspace_lacks_gets_none(self) -> None:
        block = block_for({"goal"})
        assert "Plans" not in block
        assert "course_unit" not in block

    def test_an_empty_workspace_says_so_rather_than_rendering_nothing(self) -> None:
        block = block_for(set())
        assert BEGIN in block and END in block
        assert "as content arrives" in block

    def test_units_and_graded_work_follow_the_hand_written_idiom(self) -> None:
        # Units sort by unit, and graded work splits what is due from what is
        # graded, as a hand-written course dashboard would.
        block = block_for({"course_unit", "course_assignment"})
        assert "## Units" in block
        assert "## Graded Work" in block
        assert 'lifecycle != "graded"' in block
        assert 'lifecycle == "graded"' in block

    def test_activity_stays_folder_bounded_and_newest_first(self) -> None:
        # core/01 section 13: a rolled-up year moves to 90_Archive but keeps
        # naming this workspace, so without the folder bound archived history
        # comes back.
        block = block_for({"activity_log"})
        assert "file.inFolder(this.file.folder)" in block
        assert "direction: DESC" in block
        assert "limit: 15" in block

    def test_children_are_found_by_parent_rather_than_workspace(self) -> None:
        block = block_for({"workspace"})
        assert "- parent == this.id" in block

    def test_every_workspace_homed_type_has_a_view(self) -> None:
        # Partial coverage must not be silent. This holds the table to the
        # registry, so a new type cannot ship without either a view or a
        # deliberate exemption recorded here.
        homed = {
            name
            for name, spec in TYPE_REGISTRY.items()
            if any(location.kind is LocationKind.WORKSPACE_SECTION for location in spec.locations)
        }
        covered = {embed.type_name for embed in dashboard.EMBEDS}
        assert homed <= covered, f"no dashboard view for: {sorted(homed - covered)}"

    def test_every_view_is_scoped_to_this_workspace(self) -> None:
        # One generator, every workspace sees only its own: the filter
        # follows identity, not path, so a rename or move changes nothing.
        block = block_for({embed.type_name for embed in dashboard.EMBEDS})
        for base in block.split("```base")[1:]:
            assert "this.id" in base.split("```")[0]

    def test_decisions_surface_lifecycle_and_authority(self) -> None:
        # What the startup pack ranks by is what a person should see at a
        # glance.
        block = block_for({"decision"})
        assert "lifecycle" in block and "authority" in block

    def test_only_recent_activity_is_folder_bounded(self) -> None:
        # Goals, Plans and Decisions are never archived out from under a live
        # workspace, so bounding them would serve no purpose.
        block = block_for({embed.type_name for embed in dashboard.EMBEDS})
        assert block.count("file.inFolder(this.file.folder)") == 1

    def test_views_appear_in_the_declared_order(self) -> None:
        block = block_for({"decision", "goal", "activity_log"})
        assert (
            block.index("## Goals")
            < block.index("## Decisions")
            < block.index("## Recent Activity")
        )


class TestSplicing:
    def test_prose_outside_the_markers_is_untouched(self) -> None:
        text = (
            f"---\ntype: workspace\n---\n\n# T\n\nHand prose.\n\n"
            f"{block_for(set())}\n\n## Later\n\nMore prose.\n"
        )
        new = block_for({"goal"})
        result = spliced(text, new)
        assert "Hand prose." in result
        assert "## Later" in result
        assert "More prose." in result
        assert current_block(result) == new

    def test_a_file_with_no_block_gains_one_at_the_end(self) -> None:
        text = "---\ntype: workspace\n---\n\n# T\n\nProse.\n"
        result = spliced(text, block_for({"goal"}))
        assert result.startswith("---\ntype: workspace\n---")
        assert result.index("Prose.") < result.index(BEGIN)

    def test_a_stray_marker_is_absorbed_rather_than_doubled(self) -> None:
        text = f"# T\n\n{BEGIN}\n\ntruncated\n"
        result = spliced(text, block_for(set()))
        assert result.count(BEGIN) == 1

    def test_matches_is_byte_equality(self) -> None:
        block = block_for({"goal"})
        assert matches(f"# T\n\n{block}\n", block)
        assert not matches(f"# T\n\n{block.replace('Goals', 'goals')}\n", block)


class TestRefresh:
    def _vault(self) -> tuple[InMemoryVaultFileStore, list[StoredDocument]]:
        files = InMemoryVaultFileStore()
        concepts = [workspace(W), doc(f"{W}/Goals/ship.md", "goal")]
        files.write_text(
            VaultPath.parse(f"{W}/workspace.md"),
            f"---\ntype: workspace\n---\n\n# Example\n\nProse.\n\n{block_for(set())}\n",
        )
        return files, concepts

    def test_a_drifted_block_is_rewritten(self) -> None:
        files, concepts = self._vault()
        assert refresh(files, concepts) == 1
        text = files.read_text(VaultPath.parse(f"{W}/workspace.md"))
        assert text is not None and "## Goals" in text
        assert "Prose." in text

    def test_a_current_block_is_left_alone(self) -> None:
        files, concepts = self._vault()
        refresh(files, concepts)
        assert refresh(files, concepts) == 0

    def test_within_bounds_which_dashboards_may_be_written(self) -> None:
        # Creating a document is not a licence to rewrite the dashboards of
        # workspaces it did not touch.
        files, concepts = self._vault()
        other = "10_Workspaces/Elsewhere"
        concepts.append(workspace(other))
        files.write_text(
            VaultPath.parse(f"{other}/workspace.md"),
            f"---\ntype: workspace\n---\n\n# Elsewhere\n\n{block_for({'goal'})}\n",
        )
        written = refresh(files, concepts, within={VaultPath.parse(W)})
        assert written == 1
        text = files.read_text(VaultPath.parse(f"{other}/workspace.md"))
        assert text is not None and "## Goals" in text  # wrong, but out of scope

    def test_a_manifest_file_that_does_not_exist_is_skipped(self) -> None:
        files = InMemoryVaultFileStore()
        assert refresh(files, [workspace(W)]) == 0

    def test_outdated_and_missing_are_told_apart(self) -> None:
        files, concepts = self._vault()
        other = "10_Workspaces/Handwritten"
        concepts.append(workspace(other))
        files.write_text(
            VaultPath.parse(f"{other}/workspace.md"),
            "---\ntype: workspace\n---\n\n# Handwritten\n\nNo markers here.\n",
        )
        assert dashboard.outdated(files, concepts) == [VaultPath.parse(f"{W}/workspace.md")]
        assert dashboard.missing(files, concepts) == [VaultPath.parse(f"{other}/workspace.md")]
