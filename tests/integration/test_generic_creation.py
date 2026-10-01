"""One creation verb over the whole Type Registry.

Name a type and a title, and Never4gA works out where it belongs from `core/02`
section 21 alone.

Two rules govern the placement, and both are here as tests:

- **The folder is inferred and the inference is reported.** A type's first
  registered location is its home; the arguments narrow the candidates before
  that rule applies.
- **A guess is refused.** Where a type has several equal homes, the caller
  names one and Never4gA does not pick.

The service validates before it writes and refuses anything invalid. `core/02`
section 23 governs repair, not creation: writing a document you already know is
invalid is not reporting, it is littering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.schema import TYPE_REGISTRY, ValidationLevel, validate_document
from never4ga.services import ConceptCreationError, ContentService, VaultInitializer
from never4ga.services.scaffold import shaped_types

WORKSPACE_DIRECTORY = "10_Workspaces/BezaCore"


def fixed_clock() -> datetime:
    return datetime(2026, 8, 24, 19, 0, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def files(root: Path) -> FileSystemVaultFileStore:
    return FileSystemVaultFileStore(root)


@pytest.fixture
def documents(root: Path) -> FileSystemMarkdownStore:
    return FileSystemMarkdownStore(root)


@pytest.fixture
def content(files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore) -> ContentService:
    VaultInitializer(files, documents, now=fixed_clock).initialize("Test Vault")
    documents.refresh()
    return ContentService(files, documents, now=fixed_clock)


@pytest.fixture
def workspace(content: ContentService) -> ConceptId:
    return content.create_workspace("BezaCore", workspace_type="organization").concept_id


class TestInferringTheFolder:
    def test_a_type_with_one_home_needs_nothing_but_a_title(self, content: ContentService) -> None:
        created = content.create_concept("knowledge", "Hybrid Retrieval")
        assert str(created.path) == "30_Knowledge/Notes/hybrid-retrieval.md"

    def test_a_workspace_section_type_is_placed_by_type_plus_workspace(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept("decision", "Use SQLite First", workspace=workspace)
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Decisions/use-sqlite-first.md"

    def test_the_first_registered_location_is_the_types_home(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # `plan` is legal in Plans/ and Releases/ (core/02 section 21.9).
        # Plans/ is the home.
        created = content.create_concept("plan", "Milestone 6", workspace=workspace)
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Plans/milestone-6.md"

    def test_naming_a_workspace_narrows_to_that_workspaces_sections(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # `documentation` lives in 50_System/Documentation/, a workspace's own
        # Documentation/, or its Architecture/. Naming a workspace rules out
        # the first; Documentation/ is the home of the two that remain, and
        # Architecture/ stays accepted for the design docs already there.
        created = content.create_concept("documentation", "Runtime", workspace=workspace)
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Documentation/runtime.md"

    def test_architecture_is_still_where_a_design_document_can_be_put(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        """A workspace's Architecture/ still accepts documentation."""
        created = content.create_concept(
            "documentation",
            "Runtime",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Architecture"),
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Architecture/runtime.md"

    def test_naming_no_workspace_leaves_the_vault_wide_home(self, content: ContentService) -> None:
        created = content.create_concept("standard", "Commit Messages")
        assert str(created.path) == "50_System/Standards/commit-messages.md"

    def test_punctuation_in_a_title_does_not_reach_the_filename(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # An em dash in a title must not reach the path. A spaced dash is a
        # field boundary, so it leaves an underscore rather than a hyphen:
        # `milestone-6` and `scope-questions` are two different kinds of thing.
        created = content.create_concept(
            "plan", "Milestone 6 — Scope Questions", workspace=workspace
        )
        assert created.path.name == "milestone-6_scope-questions.md"

    def test_a_dot_survives_because_a_version_is_not_punctuation(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "plan",
            "v0.1",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Releases"),
        )
        assert created.path.name == "v0.1.md"

    def test_the_inference_is_reported(self, content: ContentService) -> None:
        created = content.create_concept("knowledge", "Hybrid Retrieval")
        assert "30_Knowledge/Notes" in created.placement_reason

    def test_the_folder_appears_on_first_use(
        self, content: ContentService, workspace: ConceptId, root: Path
    ) -> None:
        content.create_concept("goal", "Ship v0.1", workspace=workspace)
        assert (root / WORKSPACE_DIRECTORY / "Goals").is_dir()


class TestRefusingToGuess:
    def test_several_equal_homes_are_refused_and_named(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # `standard` has six prescribing sections and no home among them
        # (core/02 section 21.15). Never4gA does not pick one.
        with pytest.raises(ConceptCreationError) as raised:
            content.create_concept("standard", "Palette", workspace=workspace)
        message = str(raised.value)
        assert "--in" in message
        for section in ("Strategy", "Brand", "Finance", "Operations", "Requirements", "Testing"):
            assert section in message

    def test_a_section_type_without_a_workspace_is_refused(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="workspace"):
            content.create_concept("decision", "Use SQLite First")

    def test_a_workspace_is_refused_and_points_at_its_own_verb(
        self, content: ContentService
    ) -> None:
        with pytest.raises(ConceptCreationError, match="workspace create"):
            content.create_concept("workspace", "BezaCore")

    def test_a_life_area_is_refused_and_points_at_its_own_verb(
        self, content: ContentService
    ) -> None:
        with pytest.raises(ConceptCreationError, match="life-area create"):
            content.create_concept("life_area", "Health")

    def test_the_vault_manifest_is_refused_and_points_at_init(
        self, content: ContentService
    ) -> None:
        with pytest.raises(ConceptCreationError, match="init"):
            content.create_concept("system_manifest", "Another Vault")

    def test_the_dashboard_is_refused_and_points_at_init(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="init"):
            content.create_concept("dashboard", "Home")

    def test_a_vault_cannot_be_brought_into_existence_sideways(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        """The reason the singletons are refused rather than merely awkward.

        Writing `50_System/system.md` into a directory that was never
        initialised would make it a vault: a fresh identity keying all its
        derived state (core/05 section 8), with no roots, no `home.md` and no
        templates. `init` creates a vault, and it is the only thing that does.
        """
        service = ContentService(files, documents, now=fixed_clock)
        with pytest.raises(ConceptCreationError):
            service.create_concept("system_manifest", "Not A Real Vault")
        assert not (root / "50_System" / "system.md").exists()

    def test_an_unregistered_type_is_refused(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="not a registered type"):
            content.create_concept("invention", "Something New")

    def test_creating_the_same_concept_twice_is_refused(self, content: ContentService) -> None:
        content.create_concept("knowledge", "Hybrid Retrieval")
        with pytest.raises(ConceptCreationError, match="already exists"):
            content.create_concept("knowledge", "Hybrid Retrieval")


class TestNamingTheFolder:
    def test_a_legal_alternate_home_is_accepted(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "plan",
            "v0.1",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Releases"),
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Releases/v0.1.md"

    def test_an_illegal_folder_is_refused_with_the_legal_ones_named(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        with pytest.raises(ConceptCreationError) as raised:
            content.create_concept(
                "decision",
                "Use SQLite First",
                workspace=workspace,
                in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Plans"),
            )
        assert "Decisions" in str(raised.value)

    def test_a_folder_in_another_workspace_is_refused(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        content.create_workspace("Harborview", workspace_type="project")
        with pytest.raises(ConceptCreationError, match="workspace"):
            content.create_concept(
                "decision",
                "Use SQLite First",
                workspace=workspace,
                in_directory=VaultPath.parse("10_Workspaces/Harborview/Decisions"),
            )

    def test_an_advisory_type_is_accepted_in_a_folder_off_its_list(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        """core/02 spells `resource` "Typical location": a mismatch is advice.

        `validate` honors that: a resource moved into `Units/` by hand is a
        `misplaced_document` *warning* at strict. Refusing the same placement
        at creation would make the verb stricter than the validator, and the
        workaround (create in `Resources/`, then move the file) produces
        exactly the document the verb refused to write.
        """
        created = content.create_concept(
            "resource",
            "Unit 3 Rubric",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Units/Unit-3"),
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Units/Unit-3/unit-3-rubric.md"

    def test_the_advice_names_the_typical_home(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "resource",
            "Unit 3 Rubric",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Units/Unit-3"),
        )
        assert "typically belongs" in created.placement_reason
        assert "Resources" in created.placement_reason

    def test_a_listed_folder_for_an_advisory_type_carries_no_advice(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "resource",
            "Reading List",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Resources"),
        )
        assert "typically belongs" not in created.placement_reason

    def test_an_advisory_placement_still_validates(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "resource",
            "Unit 3 Rubric",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Units/Unit-3"),
        )
        report = validate_document(
            created.path, created.document.frontmatter, level=ValidationLevel.STRICT
        )
        assert not report.errors

    def test_an_advisory_placement_may_still_not_cross_workspaces(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        content.create_workspace("Harborview", workspace_type="project")
        with pytest.raises(ConceptCreationError, match="workspace"):
            content.create_concept(
                "resource",
                "Unit 3 Rubric",
                workspace=workspace,
                in_directory=VaultPath.parse("10_Workspaces/Harborview/Units/Unit-3"),
            )


class TestFields:
    def test_the_workspace_argument_becomes_the_workspace_field(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept("goal", "Ship v0.1", workspace=workspace)
        assert created.document.frontmatter["workspace"] == str(workspace)

    def test_lifecycle_defaults_to_the_start_of_the_types_vocabulary(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept("plan", "Milestone 6", workspace=workspace)
        assert created.document.frontmatter["lifecycle"] == "draft"

    def test_a_named_lifecycle_wins(self, content: ContentService, workspace: ConceptId) -> None:
        created = content.create_concept(
            "plan", "Milestone 6", workspace=workspace, fields={"lifecycle": "active"}
        )
        assert created.document.frontmatter["lifecycle"] == "active"

    def test_a_required_field_never4ga_cannot_know_is_refused_by_name(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # When a thing happened is content. Never4gA does not invent it, for the
        # same reason core/02 section 5.2 forbids fabricating `generated.at`.
        with pytest.raises(ConceptCreationError, match="occurred_at"):
            content.create_concept("activity_log", "Session", workspace=workspace)

    def test_a_supplied_required_field_is_written(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "activity_log",
            "Session",
            workspace=workspace,
            fields={"occurred_at": "2026-08-24T19:00:00Z"},
        )
        assert created.document.frontmatter["occurred_at"] == "2026-08-24T19:00:00Z"

    def test_a_list_valued_field_survives_being_given_once(self, content: ContentService) -> None:
        created = content.create_concept(
            "knowledge", "Hybrid Retrieval", fields={"tags": "retrieval"}
        )
        assert created.document.frontmatter["tags"] == ["retrieval"]

    def test_a_list_valued_field_given_several_times_stays_a_list(
        self, content: ContentService
    ) -> None:
        created = content.create_concept(
            "knowledge", "Hybrid Retrieval", fields={"tags": ["retrieval", "sqlite"]}
        )
        assert created.document.frontmatter["tags"] == ["retrieval", "sqlite"]

    def test_a_description_is_carried(self, content: ContentService) -> None:
        created = content.create_concept(
            "knowledge", "Hybrid Retrieval", description="BM25 and vectors together"
        )
        assert created.document.frontmatter["description"] == "BM25 and vectors together"

    def test_a_field_that_would_make_the_document_invalid_is_refused(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        with pytest.raises(ConceptCreationError, match="lifecycle"):
            content.create_concept(
                "plan", "Milestone 6", workspace=workspace, fields={"lifecycle": "proposed"}
            )

    @pytest.mark.parametrize("name", ["title", "description"])
    def test_a_field_repeating_an_argument_is_refused_by_name(
        self, content: ContentService, name: str
    ) -> None:
        # create takes the title and the description as arguments of its own,
        # so a field naming either again contradicts them rather than refining
        # them.
        with pytest.raises(ConceptCreationError, match=name):
            content.create_concept("knowledge", "Hybrid Retrieval", fields={name: "Other"})

    def test_nothing_is_written_when_a_field_repeats_an_argument(
        self, content: ContentService, root: Path
    ) -> None:
        with pytest.raises(ConceptCreationError):
            content.create_concept("knowledge", "Hybrid Retrieval", fields={"title": "Other"})
        assert not (root / "30_Knowledge/Notes/hybrid-retrieval.md").exists()
        assert not (root / "30_Knowledge/Notes/other.md").exists()


class TestCreatingIntoALifeArea:
    """An area holds documents, scoped by `area` (core/01 section 7, core/02 section 16.3).

    Which area is never inferred. An area location is a subtree rather than a
    directory (grouping inside one is allowed), so there is no single folder to
    fall into and no basis for picking between four responsibilities. The
    caller names it with `--in`, exactly as a type's equal homes are named.
    """

    @pytest.fixture
    def area(self, content: ContentService) -> ConceptId:
        return content.create_life_area("Gardening").concept_id

    def test_a_document_is_created_inside_an_area(
        self, content: ContentService, area: ConceptId
    ) -> None:
        created = content.create_concept(
            "research_note",
            "Spring Planting Plan",
            in_directory=VaultPath.parse("20_Life/Gardening"),
        )
        assert str(created.path) == "20_Life/Gardening/spring-planting-plan.md"

    def test_it_is_scoped_by_area_rather_than_workspace(
        self, content: ContentService, area: ConceptId
    ) -> None:
        created = content.create_concept(
            "research_note",
            "Spring Planting Plan",
            in_directory=VaultPath.parse("20_Life/Gardening"),
        )
        frontmatter = created.document.frontmatter
        assert frontmatter["area"] == str(area)
        assert "workspace" not in frontmatter

    def test_a_group_inside_an_area_is_accepted(
        self, content: ContentService, area: ConceptId
    ) -> None:
        """`gardening/seed-catalog/` mirrors the structure of the thing practised."""
        created = content.create_concept(
            "resource",
            "Tomatoes",
            in_directory=VaultPath.parse("20_Life/Gardening/seed-catalog"),
        )
        assert str(created.path) == "20_Life/Gardening/seed-catalog/tomatoes.md"

    def test_the_created_document_validates(self, content: ContentService, area: ConceptId) -> None:
        created = content.create_concept(
            "plan", "Season Plan", in_directory=VaultPath.parse("20_Life/Gardening")
        )
        report = validate_document(
            created.path, created.document.frontmatter, level=ValidationLevel.STRICT
        )
        assert report.ok, [issue.code for issue in report.issues]

    def test_an_area_is_never_inferred(self, content: ContentService, area: ConceptId) -> None:
        """Four responsibilities and no basis for choosing: say which one."""
        with pytest.raises(ConceptCreationError):
            content.create_concept("research_note", "Spring Planting Plan")

    def test_a_type_the_amendment_did_not_name_is_refused_in_an_area(
        self, content: ContentService, area: ConceptId
    ) -> None:
        """`knowledge` stays flat; reference is not practice (core/01 section 7)."""
        with pytest.raises(ConceptCreationError):
            content.create_concept(
                "knowledge",
                "Docker Networking",
                in_directory=VaultPath.parse("20_Life/Gardening"),
            )


class TestTheBody:
    def test_a_shaped_type_gets_its_template_shape(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept("decision", "Use SQLite First", workspace=workspace)
        # The file a person starts from the template and the file `never4ga`
        # writes are the same file.
        assert "## Context" in created.document.body
        assert "## Decision" in created.document.body
        assert "## Consequences" in created.document.body

    def test_every_creatable_type_gets_a_shape(self, content: ContentService) -> None:
        # There is no unshaped creatable type left. The fallback to a bare
        # title still exists in the service and is unreachable, which is the
        # point.
        created = content.create_concept("integration", "OpenProject")
        assert "## What it integrates" in created.document.body


class TestEveryShapedTypeIsCreatable:
    """Every registered type with a shape is creatable through one verb."""

    #: The two that keep their own front door: creating either makes a
    #: directory and an `index.md`, not just a file (core/01 section 6).
    OWN_VERB = frozenset({"workspace", "life_area"})

    #: What a type requires that Never4gA cannot know: an entity's kind, and
    #: when a logged thing happened.
    SUPPLIED: ClassVar = {
        "entity": {"entity_type": "person"},
        "activity_log": {"occurred_at": "2026-08-24T19:00:00Z"},
        # A dismissal is a judgement about a specific detected finding, and
        # none of the three is derivable: the rule that fired, the ledger's key
        # for it, and why a person overruled it (core/02 section 21.22).
        "finding_dismissal": {
            "finding_code": "repository_reference_broken",
            "fingerprint": "3f9a1c2e",
            "reason": "the document names the path in order to say it is gone",
        },
    }

    #: Types with several equal homes and no default.
    NEEDS_A_FOLDER: ClassVar = {"standard": "Strategy"}

    #: Types whose only home is a life area, so a workspace section cannot hold
    #: one. `person` (core/02 section 21.23) is the first: `20_Life/` is
    #: where people live and a person is not scoped to a project.
    NEEDS_AN_AREA: ClassVar = frozenset({"person"})

    @pytest.mark.parametrize(
        "type_name", sorted(shaped_types() - frozenset({"workspace", "life_area"}))
    )
    def test_it_is_created_and_validates_strictly(
        self,
        content: ContentService,
        workspace: ConceptId,
        type_name: str,
        root: Path,
    ) -> None:
        if type_name in self.NEEDS_AN_AREA:
            content.create_life_area("Relationships")
            created = content.create_concept(
                type_name,
                f"A {type_name}",
                in_directory=VaultPath.parse("20_Life/Relationships"),
                fields=self.SUPPLIED.get(type_name),
            )
        else:
            section = self.NEEDS_A_FOLDER.get(type_name)
            created = content.create_concept(
                type_name,
                f"A {type_name}",
                workspace=workspace,
                in_directory=(
                    VaultPath.parse(f"{WORKSPACE_DIRECTORY}/{section}") if section else None
                ),
                fields=self.SUPPLIED.get(type_name),
            )
        assert (root / str(created.path)).is_file()
        report = validate_document(
            created.path, created.document.frontmatter, level=ValidationLevel.STRICT
        )
        assert not report.errors, [issue.message for issue in report.errors]

    def test_the_registry_holds_no_shaped_type_this_test_forgot(self) -> None:
        assert shaped_types() <= frozenset(TYPE_REGISTRY)

    def test_only_machine_written_types_have_no_shape(self) -> None:
        """The invariant that closes the gap rather than patching it.

        A type registered in `core/02` section 21 and reachable through
        `concept create` must have a body shape, or a person gets a file with a
        title and nothing else.

        Three exceptions, for two different reasons. `dashboard` and
        `system_manifest` are the vault itself: pinned to one exact path, written
        by `init` as literals, and never created by a user.

        `skill_provenance` (core/02 section 21.21) is the third and the reason
        is sharper. Its required `origin` and `integrity` are nested mappings
        whose digests are computed from the subject directory, so
        `--field name=value` cannot express them and a hand-authored record
        would be wrong by construction. It is written by the vendoring
        machinery or not at all.
        """
        assert frozenset(TYPE_REGISTRY) - shaped_types() == {
            "dashboard",
            "system_manifest",
            "skill_provenance",
        }


class TestActivityLogsAreDatedAndFolderedByYear:
    """core/01 section 13: `Logs/<YYYY>/`, and filenames keep a full date prefix.

    The date comes from `occurred_at`, which is a required field on
    `activity_log`: the log names the day it is *about*, not the day somebody
    got round to writing it.
    """

    def test_it_lands_in_the_year_folder_with_a_date_prefix(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        created = content.create_concept(
            "activity_log",
            "Session Handoff",
            workspace=workspace,
            fields={"occurred_at": "2026-08-25T09:58:57-04:00"},
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Logs/2026/2026-08-25_session-handoff.md"

    def test_naming_the_folder_does_not_lose_the_date_prefix(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # `--in` picks the directory; it does not change what an activity_log
        # is called. The naming rule belongs to the type, not to how the folder
        # was arrived at -- and passing the year folder explicitly is the
        # obvious thing to do when one already exists.
        created = content.create_concept(
            "activity_log",
            "Session Handoff",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Logs/2026"),
            fields={"occurred_at": "2026-08-25T09:58:57-04:00"},
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Logs/2026/2026-08-25_session-handoff.md"

    def test_a_title_carrying_its_own_date_does_not_state_it_twice(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # Titles are written by people, and a handoff is naturally called
        # "Session Handoff -- 2026-08-25". Prefixing that verbatim yields
        # `2026-08-25_session-handoff-2026-08-25.md`.
        created = content.create_concept(
            "activity_log",
            "Session Handoff — 2026-08-25 (Milestone 6)",
            workspace=workspace,
            fields={"occurred_at": "2026-08-25T09:58:57-04:00"},
        )
        assert (
            str(created.path)
            == f"{WORKSPACE_DIRECTORY}/Logs/2026/2026-08-25_session-handoff_milestone-6.md"
        )

    def test_a_different_date_in_the_title_is_kept(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # Only the date being stated twice is redundant. A log written on one
        # day *about* another names both on purpose.
        created = content.create_concept(
            "activity_log",
            "Retrospective on 2025-01-03",
            workspace=workspace,
            fields={"occurred_at": "2026-08-25T09:58:57-04:00"},
        )
        assert (
            str(created.path)
            == f"{WORKSPACE_DIRECTORY}/Logs/2026/2026-08-25_retrospective-on-2025-01-03.md"
        )

    def test_the_year_comes_from_occurred_at_not_from_today(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # The clock is fixed at 2026; a log about last year still files there.
        created = content.create_concept(
            "activity_log",
            "Old News",
            workspace=workspace,
            fields={"occurred_at": "2025-01-03T12:00:00+00:00"},
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Logs/2025/2025-01-03_old-news.md"

    def test_the_offset_decides_the_day(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # 2026-01-01T00:30+05:00 is still 2025 in UTC. The date a person wrote
        # is the date they meant, so the offset is respected rather than
        # normalised away -- otherwise a log files under the wrong year.
        created = content.create_concept(
            "activity_log",
            "New Year",
            workspace=workspace,
            fields={"occurred_at": "2026-01-01T00:30:00+05:00"},
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Logs/2026/2026-01-01_new-year.md"

    def test_a_date_without_an_offset_is_still_refused_by_the_schema(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # The placement rule reads a bare date happily, but core/02 requires
        # occurred_at to carry an explicit offset. Relaxing the schema to make
        # placement convenient would be the tail wagging the dog, so the
        # document is refused and the two rules stay independent.
        with pytest.raises(ConceptCreationError, match="offset"):
            content.create_concept(
                "activity_log",
                "Plain Date",
                workspace=workspace,
                fields={"occurred_at": "2026-08-25"},
            )

    def test_the_document_validates_where_it_landed(
        self, content: ContentService, workspace: ConceptId, documents: FileSystemMarkdownStore
    ) -> None:
        # A year subfolder must still satisfy the registered Logs/ location,
        # or the verb would write something `doctor` immediately calls misplaced.
        created = content.create_concept(
            "activity_log",
            "Valid Here",
            workspace=workspace,
            fields={"occurred_at": "2026-08-25T09:00:00-04:00"},
        )
        documents.refresh()
        stored = documents.get_by_path(created.path)
        assert stored is not None
        report = validate_document(stored.path, stored.frontmatter, level=ValidationLevel.STRICT)
        assert report.ok, [str(issue) for issue in report.issues]

    def test_an_unparseable_occurred_at_is_refused_rather_than_guessed(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # Falling back to "today" would file the log under a year it is not
        # about, which is the failure this whole change exists to prevent.
        with pytest.raises(ConceptCreationError, match="occurred_at"):
            content.create_concept(
                "activity_log", "Nonsense", workspace=workspace, fields={"occurred_at": "last week"}
            )

    def test_naming_a_folder_explicitly_still_wins(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        # `--in` is the caller overriding inference; it keeps doing that.
        created = content.create_concept(
            "activity_log",
            "Rolled Up",
            workspace=workspace,
            in_directory=VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Logs"),
            fields={"occurred_at": "2026-12-31T23:59:00-05:00"},
        )
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Logs/rolled-up.md"

    def test_other_types_are_untouched(self, content: ContentService, workspace: ConceptId) -> None:
        created = content.create_concept("plan", "Milestone 7", workspace=workspace)
        assert str(created.path) == f"{WORKSPACE_DIRECTORY}/Plans/milestone-7.md"


class TestTheRegistryAndTheVerbAgree:
    """`TypeSpec.creatable` is a second statement of what the verb refuses.

    The verb's refusal names which other verb to use, so the *reason* has to
    live with it; a client asking "may I offer this type?" must not have to
    provoke an error to find out, so the *fact* has to be published too. Two
    statements of one rule can drift apart, so they are compared rather than
    trusted.
    """

    def test_every_type_the_registry_calls_creatable_can_be_created(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        refused = []
        for name, spec in sorted(TYPE_REGISTRY.items()):
            if not spec.creatable:
                continue
            try:
                content.create_concept(
                    name,
                    f"Agreement Probe {name}",
                    workspace=workspace,
                    in_directory=None,
                    fields={"occurred_at": "2026-09-09T12:00:00Z"},
                )
            except ConceptCreationError as error:
                # A type needing a folder named, or a field only a person
                # knows, is still creatable -- it just needs more arguments.
                # Being sent to another verb is what `creatable` denies.
                if "use `never4ga" in str(error) or "`never4ga init` writes it" in str(error):
                    refused.append(f"{name}: {error}")
        assert refused == []

    def test_every_type_the_registry_calls_uncreatable_is_sent_elsewhere(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        for name, spec in sorted(TYPE_REGISTRY.items()):
            if spec.creatable:
                continue
            with pytest.raises(ConceptCreationError) as raised:
                content.create_concept(name, f"Probe {name}", workspace=workspace)
            message = str(raised.value)
            assert "use `never4ga" in message or "`never4ga init` writes it" in message, name


#: A supplied value for each field Never4gA writes, well-formed where that
#: matters: a malformed one might be refused by accident, and the point is that
#: even a plausible value is not the caller's to give.
OWNED_FIELD_VALUES: tuple[tuple[str, object], ...] = (
    ("type", "garbage"),
    ("id", str(ConceptId.new())),
    ("schema", "garbage"),
    ("generated", {"by": "someone/1.0", "at": "2026-09-01T08:00:00Z"}),
)


class TestFieldsNever4gAWrites:
    """A supplied owned field is refused.

    `id` is identity, which Never4gA mints; `generated` is provenance core/02
    section 5.2 says is never fabricated; `type` and `schema` are what
    Never4gA resolved and reports. Refusing rather than ignoring treats the
    field the way a repeated title is treated: a caller who supplied one made
    a mistake worth hearing about.
    """

    @pytest.mark.parametrize(("name", "value"), OWNED_FIELD_VALUES)
    def test_an_owned_field_is_refused_and_nothing_is_written(
        self, content: ContentService, root: Path, name: str, value: object
    ) -> None:
        before = sorted(root.rglob("*.md"))
        with pytest.raises(ConceptCreationError, match=rf"^{name} is written by Never4gA"):
            content.create_concept("knowledge", "Owned Fields", fields={name: value})
        assert sorted(root.rglob("*.md")) == before

    def test_every_owned_field_supplied_is_named_at_once(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match=r"^type and id are written by Never4gA"):
            content.create_concept("knowledge", "Owned Fields", fields={"id": "x", "type": "x"})

    def test_a_supplied_created_at_is_kept(self, content: ContentService) -> None:
        # Not on the list: when a document was written is a fact about the
        # content, and an honest date for an older document is legitimate.
        created = content.create_concept(
            "knowledge", "Written Earlier", fields={"created_at": "2026-09-01T08:00:00Z"}
        )
        assert created.document.frontmatter["created_at"] == "2026-09-01T08:00:00Z"

    def test_a_created_at_that_is_not_a_timestamp_is_refused(
        self, content: ContentService, root: Path
    ) -> None:
        before = sorted(root.rglob("*.md"))
        with pytest.raises(ConceptCreationError, match="created_at"):
            content.create_concept("knowledge", "Written Earlier", fields={"created_at": "soon"})
        assert sorted(root.rglob("*.md")) == before

    def test_provenance_comes_in_through_the_actor_argument(self, content: ContentService) -> None:
        # What `wrap` needs: a log credited to the producer the session
        # recorded.
        created = content.create_concept(
            "knowledge", "Written for Someone", actor="claude-code/claude-opus-5"
        )
        assert created.document.frontmatter["generated"]["by"] == "claude-code/claude-opus-5"
