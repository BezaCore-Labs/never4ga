"""Vault layout rules (core/01).

These are pure path rules: which roots exist, which filenames are reserved,
where a workspace begins and ends, and which areas must stay flat. Nothing here
touches a filesystem.
"""

from __future__ import annotations

import pytest

from never4ga.domain.document import VaultPath
from never4ga.layout import (
    ARCHIVE_SECTIONS,
    AREA_MANIFEST,
    HOME,
    KNOWLEDGE_DIRECTORIES,
    RESERVED_INDEX,
    RESERVED_LOG,
    ROOT_INDEX,
    SYSTEM_DIRECTORIES,
    SYSTEM_MANIFEST,
    WORKSPACE_BASE_DIRECTORIES,
    WORKSPACE_MANIFEST,
    DocumentRole,
    FlatArea,
    VaultRoot,
    archived_counterpart,
    flat_area_of,
    is_foreign_format,
    is_foreign_note,
    is_life_area_content,
    life_area_directory_of,
    registered_foreign_material,
    role_of,
    workspace_directory_of,
    workspace_section_of,
)


def p(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


class TestRoots:
    def test_there_are_exactly_seven_stable_roots(self) -> None:
        assert [root.value for root in VaultRoot] == [
            "00_Inbox",
            "10_Workspaces",
            "20_Life",
            "30_Knowledge",
            "50_System",
            "90_Archive",
        ]

    def test_the_numeric_prefixes_keep_the_roots_ordered(self) -> None:
        values = [root.value for root in VaultRoot]
        assert values == sorted(values)

    def test_the_root_level_files_are_named(self) -> None:
        assert str(ROOT_INDEX) == "index.md"
        assert str(HOME) == "home.md"

    def test_the_system_manifest_is_the_vault_identity_document(self) -> None:
        # core/01 section 10: "system.md is the stable vault/system manifest."
        assert str(SYSTEM_MANIFEST) == "50_System/system.md"


class TestReservedNames:
    def test_the_reserved_filenames_are_index_and_log(self) -> None:
        assert RESERVED_INDEX == "index.md"
        assert RESERVED_LOG == "log.md"

    def test_the_manifest_filenames_are_distinct_from_index(self) -> None:
        # core/01 section 4: semantic containers use a separate manifest concept.
        assert WORKSPACE_MANIFEST == "workspace.md"
        assert AREA_MANIFEST == "area.md"
        assert RESERVED_INDEX not in {WORKSPACE_MANIFEST, AREA_MANIFEST}


class TestDocumentRole:
    def test_the_root_index_is_the_okf_bundle_entry_point(self) -> None:
        # core/02 section 4: the only index.md permitted to carry frontmatter.
        assert role_of(p("index.md")) is DocumentRole.ROOT_INDEX

    @pytest.mark.parametrize(
        "raw",
        [
            "30_Knowledge/index.md",
            "10_Workspaces/BezaCore/index.md",
            "20_Life/Health/index.md",
            "10_Workspaces/BezaCore/Workspaces/Sparrow/index.md",
        ],
    )
    def test_a_directory_index_is_reserved_navigation(self, raw: str) -> None:
        assert role_of(p(raw)) is DocumentRole.DIRECTORY_INDEX

    @pytest.mark.parametrize("raw", ["log.md", "10_Workspaces/BezaCore/log.md"])
    def test_log_md_is_reserved_directory_history(self, raw: str) -> None:
        # core/01 section 13: never name an activity record log.md.
        assert role_of(p(raw)) is DocumentRole.DIRECTORY_LOG

    @pytest.mark.parametrize(
        "raw",
        [
            "home.md",
            "10_Workspaces/BezaCore/workspace.md",
            "20_Life/Health/area.md",
            "50_System/system.md",
            "30_Knowledge/Notes/Hybrid Retrieval.md",
            "10_Workspaces/BezaCore/Logs/2026-08-22 session.md",
        ],
    )
    def test_everything_else_is_an_ordinary_concept(self, raw: str) -> None:
        assert role_of(p(raw)) is DocumentRole.CONCEPT

    def test_a_workspace_manifest_is_a_concept_while_its_index_is_not(self) -> None:
        # The workspace.md vs index.md distinction (core/01 section 4).
        assert role_of(p("10_Workspaces/BezaCore/workspace.md")) is DocumentRole.CONCEPT
        assert role_of(p("10_Workspaces/BezaCore/index.md")) is DocumentRole.DIRECTORY_INDEX

    def test_a_logs_entry_is_a_concept_but_log_md_is_not(self) -> None:
        logs_entry = p("10_Workspaces/BezaCore/Logs/kickoff.md")
        assert role_of(logs_entry) is DocumentRole.CONCEPT
        assert role_of(p("10_Workspaces/BezaCore/Logs/log.md")) is DocumentRole.DIRECTORY_LOG


class TestWorkspaceResolution:
    def test_a_top_level_workspace_is_found(self) -> None:
        found = workspace_directory_of(p("10_Workspaces/BezaCore/workspace.md"))
        assert found is not None
        assert str(found) == "10_Workspaces/BezaCore"

    def test_the_workspace_directory_itself_resolves_to_itself(self) -> None:
        found = workspace_directory_of(p("10_Workspaces/BezaCore"))
        assert found is not None
        assert str(found) == "10_Workspaces/BezaCore"

    def test_content_inside_a_section_resolves_to_its_workspace(self) -> None:
        found = workspace_directory_of(p("10_Workspaces/BezaCore/Goals/ship-v1.md"))
        assert found is not None
        assert str(found) == "10_Workspaces/BezaCore"

    def test_a_child_workspace_resolves_to_itself_not_its_parent(self) -> None:
        # core/01 section 6: all child workspaces physically live under Workspaces/.
        found = workspace_directory_of(
            p("10_Workspaces/BezaCore/Workspaces/Sparrow/Goals/launch.md")
        )
        assert found is not None
        assert str(found) == "10_Workspaces/BezaCore/Workspaces/Sparrow"

    def test_nesting_goes_deeper_than_one_level(self) -> None:
        found = workspace_directory_of(
            p("10_Workspaces/A/Workspaces/B/Workspaces/C/Decisions/d.md")
        )
        assert found is not None
        assert str(found) == "10_Workspaces/A/Workspaces/B/Workspaces/C"

    def test_the_child_container_alone_resolves_to_the_parent(self) -> None:
        found = workspace_directory_of(p("10_Workspaces/BezaCore/Workspaces"))
        assert found is not None
        assert str(found) == "10_Workspaces/BezaCore"

    def test_an_archived_workspace_still_resolves(self) -> None:
        found = workspace_directory_of(p("90_Archive/Workspaces/Old Thing/workspace.md"))
        assert found is not None
        assert str(found) == "90_Archive/Workspaces/Old Thing"

    @pytest.mark.parametrize(
        "raw",
        [
            "10_Workspaces",
            "10_Workspaces/index.md",
            "30_Knowledge/Notes/thing.md",
            "20_Life/Health/area.md",
            "home.md",
        ],
    )
    def test_paths_outside_any_workspace_resolve_to_nothing(self, raw: str) -> None:
        assert workspace_directory_of(p(raw)) is None


class TestWorkspacesInsideALifeArea:
    """A workspace in a life area is a bounded pursuit belonging to a responsibility.

    ``20_Life/<Area>/Workspaces/`` works exactly as a workspace's own
    ``Workspaces/`` does (core/01 section 7, core/03 section 30). The area
    itself is never a workspace: it is carried, not pursued.
    """

    def test_a_workspace_under_an_area_resolves_to_itself(self) -> None:
        found = workspace_directory_of(p("20_Life/Education/Workspaces/MATH-101/workspace.md"))
        assert found is not None
        assert str(found) == "20_Life/Education/Workspaces/MATH-101"

    def test_content_inside_it_belongs_to_the_workspace_not_the_area(self) -> None:
        found = workspace_directory_of(
            p("20_Life/Education/Workspaces/MATH-101/Goals/pass-the-final.md")
        )
        assert found is not None
        assert str(found) == "20_Life/Education/Workspaces/MATH-101"

    def test_it_nests_the_same_way_a_workspace_child_does(self) -> None:
        found = workspace_directory_of(
            p("20_Life/Education/Workspaces/Northfield/Workspaces/MATH-101/Tasks/t.md")
        )
        assert found is not None
        assert str(found) == "20_Life/Education/Workspaces/Northfield/Workspaces/MATH-101"

    def test_a_section_inside_an_area_workspace_is_reported(self) -> None:
        path = p("20_Life/Education/Workspaces/MATH-101/Goals/pass-the-final.md")
        assert workspace_section_of(path) == "Goals"

    @pytest.mark.parametrize(
        "raw",
        [
            # The container is not itself a workspace, and unlike a workspace's
            # own Workspaces/ there is no parent workspace above it to fall back
            # to -- an area is not one.
            "20_Life/Education/Workspaces",
            "20_Life/Education/Workspaces/index.md",
            # Area material is not a workspace, however deeply it is grouped.
            "20_Life/Gardening/seed-catalog/tomatoes.md",
            "20_Life/Reading/classics/war-and-peace/chapter-001.md",
            "20_Life/Education/area.md",
        ],
    )
    def test_what_is_not_a_workspace_under_an_area(self, raw: str) -> None:
        assert workspace_directory_of(p(raw)) is None


class TestWorkspaceSections:
    def test_the_base_directories_match_the_specification(self) -> None:
        # core/01 section 6 and core/03 section 2.
        assert WORKSPACE_BASE_DIRECTORIES == (
            "Context",
            "Goals",
            "Plans",
            "Tasks",
            "Decisions",
            "Research",
            "Resources",
            "Logs",
            "Assets",
            "Workspaces",
        )

    def test_a_section_is_reported_for_content_inside_one(self) -> None:
        assert workspace_section_of(p("10_Workspaces/BezaCore/Decisions/adr.md")) == "Decisions"

    def test_the_manifest_sits_in_no_section(self) -> None:
        assert workspace_section_of(p("10_Workspaces/BezaCore/workspace.md")) is None

    def test_a_profile_directory_is_reported_even_though_it_is_not_a_base_one(self) -> None:
        # core/01 section 6 allows <profile extensions>/ alongside the base set.
        assert workspace_section_of(p("10_Workspaces/BezaCore/Architecture/adr.md")) == (
            "Architecture"
        )

    def test_a_child_workspace_section_belongs_to_the_child(self) -> None:
        path = p("10_Workspaces/A/Workspaces/B/Tasks/t.md")
        assert workspace_section_of(path) == "Tasks"

    def test_content_outside_a_workspace_has_no_section(self) -> None:
        assert workspace_section_of(p("30_Knowledge/Notes/thing.md")) is None


class TestLifeAreas:
    def test_a_life_area_directory_is_found(self) -> None:
        found = life_area_directory_of(p("20_Life/Health/area.md"))
        assert found is not None
        assert str(found) == "20_Life/Health"

    def test_the_life_root_itself_is_not_an_area(self) -> None:
        assert life_area_directory_of(p("20_Life/index.md")) is None

    def test_a_path_outside_life_has_no_area(self) -> None:
        assert life_area_directory_of(p("10_Workspaces/BezaCore/workspace.md")) is None


class TestLifeAreaContent:
    """An area holds its own working material (core/01 section 7).

    Grouping inside an area is allowed when it mirrors the structure of the
    thing being practised (core/01 section 7), so this is a subtree rule rather
    than a directory one. What it must *not* swallow is the area's own
    `Workspaces/`: content there belongs to a workspace and is judged as such,
    or a goal misfiled into a course's Research/ would read as valid area
    material.
    """

    @pytest.mark.parametrize(
        "raw",
        [
            "20_Life/Gardening/spring-planting-plan.md",
            "20_Life/Gardening/seed-catalog/tomatoes.md",
            "20_Life/Reading/classics/war-and-peace/chapter-001.001-017.md",
            "20_Life/Career/cover-letter-acme.md",
        ],
    )
    def test_material_inside_an_area_is_area_content(self, raw: str) -> None:
        assert is_life_area_content(p(raw)) is True

    @pytest.mark.parametrize(
        "raw",
        [
            # The manifest and the reserved OKF filenames are not material.
            "20_Life/Gardening/area.md",
            "20_Life/Gardening/index.md",
            "20_Life/Gardening/log.md",
            # The Life root itself holds no area material.
            "20_Life/index.md",
            # A bounded pursuit's content belongs to the pursuit.
            "20_Life/Education/Workspaces/MATH-101/workspace.md",
            "20_Life/Education/Workspaces/MATH-101/Research/unit-6.md",
            # Other roots entirely.
            "30_Knowledge/Notes/docker-networking.md",
            "10_Workspaces/BezaCore/Goals/ship.md",
        ],
    )
    def test_what_is_not_area_content(self, raw: str) -> None:
        assert is_life_area_content(p(raw)) is False


class TestFlatAreas:
    def test_knowledge_notes_is_a_flat_area(self) -> None:
        # core/01 section 8: Notes/ remains semantically flat.
        assert flat_area_of(p("30_Knowledge/Notes/Hybrid Retrieval.md")) is FlatArea.KNOWLEDGE_NOTES

    def test_knowledge_maps_is_a_flat_area(self) -> None:
        assert flat_area_of(p("30_Knowledge/Maps/AI.md")) is FlatArea.KNOWLEDGE_MAPS

    @pytest.mark.parametrize(
        "raw",
        [
            "30_Knowledge/Notes/Programming/Python/thing.md",
            "30_Knowledge/Maps/AI/Retrieval.md",
        ],
    )
    def test_a_nested_path_still_reports_its_flat_area(self, raw: str) -> None:
        # Reporting the area is the layout layer's job; deciding that nesting is
        # an error belongs to placement validation.
        assert flat_area_of(p(raw)) is not None

    @pytest.mark.parametrize(
        "raw",
        ["30_Knowledge/index.md", "30_Knowledge/Assets/logo.png", "10_Workspaces/A/Goals/g.md"],
    )
    def test_other_locations_are_not_flat_areas(self, raw: str) -> None:
        assert flat_area_of(p(raw)) is None


class TestForeignFormatFiles:
    """core/02 sections 2.2 and 3.3."""

    @pytest.mark.parametrize(
        "raw",
        [
            "50_System/Templates/knowledge.md",
            "50_System/Templates/workspace/base.md",
            "50_System/Skills/spec-compliance/SKILL.md",
            "50_System/Agents/claude/AGENTS.md",
            "50_System/Integrations/Obsidian/README.md",
            "50_System/Integrations/Obsidian/Bases/all.base",
        ],
    )
    def test_tool_owned_files_are_foreign_format(self, raw: str) -> None:
        assert role_of(p(raw)) is DocumentRole.FOREIGN_FORMAT
        assert is_foreign_format(p(raw))

    def test_an_integration_record_itself_is_a_concept(self) -> None:
        # core/02 section 21.19: the concept lives directly in Integrations/;
        # the tool's own files sit beneath it.
        assert role_of(p("50_System/Integrations/Obsidian.md")) is DocumentRole.CONCEPT

    @pytest.mark.parametrize(
        "raw",
        [
            "50_System/system.md",
            "50_System/Standards/Python.md",
            "50_System/Schemas/domains.md",
            "30_Knowledge/Notes/Templates.md",
        ],
    )
    def test_ordinary_system_concepts_are_not_foreign(self, raw: str) -> None:
        assert not is_foreign_format(p(raw))

    def test_a_reserved_index_wins_over_foreign_classification(self) -> None:
        # index.md stays navigation wherever it is.
        assert role_of(p("50_System/Templates/index.md")) is DocumentRole.DIRECTORY_INDEX


class TestForeignMaterial:
    """What a registration in the system manifest makes foreign (core/01 section 1)."""

    def test_markdown_under_a_registered_directory_is_a_foreign_note(self) -> None:
        assert is_foreign_note(p("Projects/garden/beds.md"), {"Projects"})

    def test_a_registered_loose_note_is_a_foreign_note(self) -> None:
        assert is_foreign_note(p("loose.md"), {"loose.md"})

    def test_an_unregistered_loose_note_is_not(self) -> None:
        assert not is_foreign_note(p("other.md"), {"loose.md", "Projects"})

    def test_a_dot_directory_is_never_read(self) -> None:
        assert not is_foreign_note(p("Projects/.trash/old.md"), {"Projects"})

    @pytest.mark.parametrize("name", [RESERVED_INDEX, HOME.name, RESERVED_LOG])
    def test_a_manifest_naming_a_reserved_root_file_registers_nothing(self, name: str) -> None:
        # A corrupt or hand-edited manifest must never make the root index a
        # pile indexed by path.
        registered = registered_foreign_material({"foreign_material": [name, "Projects"]})
        assert registered == {"Projects"}
        assert not is_foreign_note(p(name), registered)

    def test_a_manifest_naming_a_root_registers_nothing(self) -> None:
        assert registered_foreign_material({"foreign_material": ["30_Knowledge"]}) == set()


class TestNoSpacesInPaths:
    """No path Never4gA creates contains a space."""

    def test_no_root_name_contains_a_space(self) -> None:
        assert not [root for root in VaultRoot if " " in root.value]

    def test_the_roots_are_underscored_and_still_ordered(self) -> None:
        values = [root.value for root in VaultRoot]
        assert values == [
            "00_Inbox",
            "10_Workspaces",
            "20_Life",
            "30_Knowledge",
            "50_System",
            "90_Archive",
        ]
        assert values == sorted(values)

    def test_no_predefined_directory_name_contains_a_space(self) -> None:
        names = [
            *WORKSPACE_BASE_DIRECTORIES,
            *KNOWLEDGE_DIRECTORIES,
            *SYSTEM_DIRECTORIES,
            *ARCHIVE_SECTIONS,
        ]
        assert not [name for name in names if " " in name]

    def test_no_reserved_or_manifest_filename_contains_a_space(self) -> None:
        names = [RESERVED_INDEX, RESERVED_LOG, WORKSPACE_MANIFEST, AREA_MANIFEST]
        names += [str(ROOT_INDEX), str(HOME), str(SYSTEM_MANIFEST)]
        assert not [name for name in names if " " in name]

    def test_no_flat_area_path_contains_a_space(self) -> None:
        assert not [area for area in FlatArea if " " in area.value]


class TestArchivedCounterpart:
    """Where a workspace directory's contents sit once archived (core/01 section 11)."""

    def test_a_workspace_directory_keeps_its_path_below_the_root(self) -> None:
        found = archived_counterpart(p("10_Workspaces/BezaCore/Decisions"))
        assert found == p("90_Archive/Workspaces/BezaCore/Decisions")

    def test_a_child_workspace_keeps_its_nesting(self) -> None:
        found = archived_counterpart(p("10_Workspaces/BezaCore/Workspaces/Tools/Decisions"))
        assert found == p("90_Archive/Workspaces/BezaCore/Workspaces/Tools/Decisions")

    @pytest.mark.parametrize(
        "directory",
        [
            "10_Workspaces",
            "20_Life/Health/Decisions",
            "30_Knowledge/Notes",
            "90_Archive/Workspaces",
        ],
    )
    def test_anything_else_has_none(self, directory: str) -> None:
        assert archived_counterpart(p(directory)) is None
