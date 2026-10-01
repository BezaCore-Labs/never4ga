"""Schema v0.1 registry: types, vocabularies and canonical locations (core/02).

core/02 section 22 describes the controlled registries. The core values the
specification defines are hard-coded, which section 22 explicitly permits
("Initial bootstrapping may hard-code the core values defined in this
specification"). They are not yet represented as `registry` concepts inside
`50_System/Schemas/`.
"""

from __future__ import annotations

from typing import Final

import pytest

from never4ga.domain.document import VaultPath
from never4ga.schema import (
    AUTHORITY_VALUES,
    PRIORITY_VALUES,
    RELATION_TYPES,
    SCHEMA_VERSION,
    STATUS_VALUES,
    WORKSPACE_TYPE_VALUES,
    LocationKind,
    type_spec,
)
from never4ga.schema.types import TYPE_REGISTRY


def p(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


class TestSchemaIdentifier:
    def test_the_schema_version_is_never4ga_0_1(self) -> None:
        assert SCHEMA_VERSION == "never4ga/0.1"


#: core/02 section 21 registers these types, numbered 21.1 to 21.26. 21.6
#: `entity` is retired, so it is a retired number rather than a gap.
EXPECTED_TYPES: Final = frozenset(
    {
        "dashboard",
        "workspace",
        "life_area",
        "knowledge",
        "map",
        "context",
        "goal",
        "plan",
        "task",
        "decision",
        "research_note",
        "resource",
        "activity_log",
        "standard",
        "schema_definition",
        "registry",
        "documentation",
        "integration",
        "system_manifest",
        "skill_provenance",
        "finding_dismissal",
        "person",
        "course_unit",
        "course_assignment",
        "runbook",
    }
)


class TestTypeRegistry:
    def test_every_registered_type_is_present(self) -> None:
        assert set(TYPE_REGISTRY) == EXPECTED_TYPES

    def test_there_are_twenty_five_of_them(self) -> None:
        # core/02 sections 21.1 to 21.26, less the retired 21.6 `entity`.
        # Registration and retirement are both deliberate, so this count only
        # changes when section 21 does.
        assert len(TYPE_REGISTRY) == 25

    @pytest.mark.parametrize("name", sorted(EXPECTED_TYPES))
    def test_every_type_name_is_snake_case(self, name: str) -> None:
        assert name == name.lower()
        assert " " not in name and "-" not in name

    def test_an_unknown_type_has_no_spec(self) -> None:
        # core/02 section 5.1: consumers MUST tolerate unknown type values, so
        # this is a lookup miss rather than an exception.
        assert type_spec("some_future_type") is None


class TestRequiredFieldsBeyondBase:
    @pytest.mark.parametrize(
        ("type_name", "field"),
        [
            ("workspace", "workspace_type"),
            ("workspace", "lifecycle"),
            ("life_area", "lifecycle"),
            ("context", "workspace"),
            ("goal", "workspace"),
            ("goal", "lifecycle"),
            ("plan", "workspace"),
            ("task", "workspace"),
            ("decision", "workspace"),
            ("research_note", "workspace"),
            ("activity_log", "workspace"),
            ("activity_log", "occurred_at"),
        ],
    )
    def test_the_field_is_required(self, type_name: str, field: str) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        assert field in spec.required_fields

    @pytest.mark.parametrize("type_name", ["dashboard", "knowledge", "map", "standard"])
    def test_types_with_no_extra_requirements_require_nothing_beyond_base(
        self, type_name: str
    ) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        assert spec.required_fields == ()

    def test_every_workspace_scoped_type_requires_the_workspace_field(self) -> None:
        # core/02 section 16.1 names them explicitly.
        scoped = ["goal", "plan", "task", "decision", "research_note", "context", "activity_log"]
        for name in scoped:
            spec = type_spec(name)
            assert spec is not None
            assert "workspace" in spec.required_fields, name


AREA_TYPES: Final = (
    "resource",
    "activity_log",
    "plan",
    "goal",
    "task",
    "research_note",
    "decision",
)


class TestALifeAreaHoldsItsOwnDocuments:
    """core/01 section 7 and the amendment at the head of core/02 section 21."""

    @pytest.mark.parametrize("type_name", AREA_TYPES)
    def test_the_seven_types_are_accepted_directly_inside_an_area(self, type_name: str) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        assert spec.accepts(p(f"20_Life/Gardening/{type_name}-example.md"))

    @pytest.mark.parametrize("type_name", AREA_TYPES)
    def test_they_are_accepted_in_a_group_inside_an_area(self, type_name: str) -> None:
        """`gardening/seed-catalog/`, `reading/classics/war-and-peace/`."""
        spec = type_spec(type_name)
        assert spec is not None
        assert spec.accepts(p(f"20_Life/Reading/classics/war-and-peace/{type_name}.md"))

    @pytest.mark.parametrize("type_name", ["knowledge", "map", "context"])
    def test_a_type_the_amendment_did_not_name_gains_nothing(self, type_name: str) -> None:
        """`knowledge` stays flat; `context` is not one of the seven.

        `person` is deliberately absent: core/02 section 21.23 puts it in an
        area, so it is one of the types the area *does* hold.
        """
        spec = type_spec(type_name)
        assert spec is not None
        assert not spec.accepts(p("20_Life/Gardening/spring-planting.md"))

    def test_a_workspace_manifest_under_an_area_is_accepted(self) -> None:
        # A bounded pursuit belonging to a responsibility (core/01 section 7).
        spec = type_spec("workspace")
        assert spec is not None
        assert spec.accepts(p("20_Life/Education/Workspaces/MATH-101/workspace.md"))

    def test_area_material_does_not_reach_into_a_courses_sections(self) -> None:
        """A goal in a course's Research/ is misplaced, not area material."""
        spec = type_spec("goal")
        assert spec is not None
        assert not spec.accepts(p("20_Life/Education/Workspaces/MATH-101/Research/g.md"))


class TestACourseAssignmentIsItsOwnType:
    """core/02 section 21.25, beside `course_unit` in section 21.24.

    Unit pages produce work and resources support it. The prompt, the
    rubric and the discussion question are the work's own record, graded and
    due, not supporting material. They live beside the unit page they belong
    to, so `Units/` at any depth is home rather than an alternate.
    """

    def test_it_is_at_home_anywhere_under_units(self) -> None:
        spec = type_spec("course_assignment")
        assert spec is not None
        course = "20_Life/Education/Workspaces/CS-202"
        assert spec.accepts(p(f"{course}/Units/unit-3-assignment.md"))
        assert spec.accepts(p(f"{course}/Units/Unit-3/programming-assignment.md"))

    def test_it_does_not_belong_in_resources(self) -> None:
        # The syllabus stays a resource; the assignment is not one.
        spec = type_spec("course_assignment")
        assert spec is not None
        assert not spec.accepts(p("20_Life/Education/Workspaces/CS-202/Resources/rubric.md"))

    def test_its_lifecycle_ends_at_graded_where_a_units_ends_at_complete(self) -> None:
        spec = type_spec("course_assignment")
        assert spec is not None
        assert spec.lifecycle_values == ("not_started", "in_progress", "submitted", "graded")

    def test_what_is_due_and_what_it_scored_are_registered_fields(self) -> None:
        spec = type_spec("course_assignment")
        assert spec is not None
        assert spec.required_fields == ("workspace", "lifecycle")
        assert spec.extra_fields == ("unit", "due", "grade")


class TestScopeFollowsPlacement:
    """core/02 section 16.3: the rule is *name your scope*; where decides the field."""

    @pytest.mark.parametrize(
        "type_name", ["goal", "plan", "task", "decision", "research_note", "activity_log"]
    )
    def test_an_area_placed_document_requires_area_not_workspace(self, type_name: str) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        required = spec.required_fields_for(p("20_Life/Gardening/spring-planting.md"))
        assert "area" in required
        assert "workspace" not in required

    @pytest.mark.parametrize(
        "type_name", ["goal", "plan", "task", "decision", "research_note", "activity_log"]
    )
    def test_a_workspace_placed_document_is_unchanged(self, type_name: str) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        required = spec.required_fields_for(p(f"10_Workspaces/Acme/Goals/{type_name}.md"))
        assert required == spec.required_fields
        assert "area" not in required

    def test_a_documents_other_requirements_survive_the_swap(self) -> None:
        spec = type_spec("activity_log")
        assert spec is not None
        required = spec.required_fields_for(p("20_Life/Gardening/2026-08-31_meeting.md"))
        assert set(required) == {"area", "occurred_at"}

    def test_a_type_that_needs_no_scope_is_untouched(self) -> None:
        spec = type_spec("resource")
        assert spec is not None
        assert spec.required_fields_for(p("20_Life/Gardening/seed-catalog/notes.md")) == ()


class TestLifecycleVocabularies:
    @pytest.mark.parametrize(
        ("type_name", "values"),
        [
            ("workspace", ("proposed", "active", "paused", "completed", "cancelled", "retired")),
            ("life_area", ("active", "retired")),
            ("goal", ("proposed", "active", "achieved", "abandoned")),
            ("plan", ("draft", "active", "completed", "abandoned")),
            ("task", ("todo", "in_progress", "blocked", "done", "cancelled")),
            ("decision", ("proposed", "accepted", "rejected", "superseded")),
            ("research_note", ("active", "complete", "abandoned")),
        ],
    )
    def test_the_vocabulary_matches_the_specification(
        self, type_name: str, values: tuple[str, ...]
    ) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        assert spec.lifecycle_values == values

    def test_lifecycle_vocabularies_are_type_specific_not_shared(self) -> None:
        # core/02 section 11: "Lifecycle vocabularies are type-specific."
        task = type_spec("task")
        decision = type_spec("decision")
        assert task is not None and decision is not None
        assert "done" in task.lifecycle_values
        assert "done" not in decision.lifecycle_values

    def test_a_type_without_a_lifecycle_declares_none(self) -> None:
        spec = type_spec("knowledge")
        assert spec is not None
        assert spec.lifecycle_values == ()


class TestCanonicalLocations:
    @pytest.mark.parametrize(
        ("type_name", "raw"),
        [
            ("dashboard", "home.md"),
            ("workspace", "10_Workspaces/BezaCore/workspace.md"),
            ("workspace", "10_Workspaces/A/Workspaces/B/workspace.md"),
            ("life_area", "20_Life/Health/area.md"),
            ("knowledge", "30_Knowledge/Notes/Hybrid Retrieval.md"),
            ("map", "30_Knowledge/Maps/AI.md"),
            ("person", "20_Life/Relationships/Ada Lovelace.md"),
            ("context", "10_Workspaces/BezaCore/Context/current.md"),
            ("goal", "10_Workspaces/BezaCore/Goals/ship.md"),
            ("plan", "10_Workspaces/BezaCore/Plans/q3.md"),
            ("task", "10_Workspaces/BezaCore/Tasks/t.md"),
            ("decision", "10_Workspaces/BezaCore/Decisions/d.md"),
            ("research_note", "10_Workspaces/BezaCore/Research/r.md"),
            ("resource", "10_Workspaces/BezaCore/Resources/spec.md"),
            ("activity_log", "10_Workspaces/BezaCore/Logs/session.md"),
            ("standard", "50_System/Standards/Python.md"),
            ("schema_definition", "50_System/Schemas/concept.md"),
            ("registry", "50_System/Schemas/domains.md"),
            ("documentation", "50_System/Documentation/cli.md"),
            ("integration", "50_System/Integrations/Obsidian.md"),
            ("system_manifest", "50_System/system.md"),
        ],
    )
    def test_the_canonical_location_accepts_the_specified_path(
        self, type_name: str, raw: str
    ) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        assert spec.accepts(p(raw))

    @pytest.mark.parametrize(
        ("type_name", "raw"),
        [
            ("knowledge", "10_Workspaces/BezaCore/Decisions/d.md"),
            ("decision", "30_Knowledge/Notes/d.md"),
            ("person", "30_Knowledge/Notes/Ada Lovelace.md"),
            ("goal", "10_Workspaces/BezaCore/Tasks/g.md"),
            ("workspace", "10_Workspaces/BezaCore/index.md"),
            ("life_area", "20_Life/Health/index.md"),
            ("standard", "50_System/Schemas/s.md"),
            ("dashboard", "30_Knowledge/Notes/home.md"),
            ("system_manifest", "50_System/Documentation/system.md"),
        ],
    )
    def test_the_canonical_location_rejects_a_misplaced_path(
        self, type_name: str, raw: str
    ) -> None:
        spec = type_spec(type_name)
        assert spec is not None
        assert not spec.accepts(p(raw))

    def test_an_archived_concept_keeps_its_placement(self) -> None:
        # core/01 section 11: archiving detaches material; it does not retype it.
        spec = type_spec("knowledge")
        assert spec is not None
        assert spec.accepts(p("90_Archive/Knowledge/Notes/Old Idea.md"))

    def test_an_archived_workspace_manifest_keeps_its_placement(self) -> None:
        spec = type_spec("workspace")
        assert spec is not None
        assert spec.accepts(p("90_Archive/Workspaces/Old Thing/workspace.md"))

    def test_a_child_workspace_section_is_owned_by_the_child(self) -> None:
        spec = type_spec("task")
        assert spec is not None
        assert spec.accepts(p("10_Workspaces/A/Workspaces/B/Tasks/t.md"))

    def test_locations_marked_typical_are_advisory(self) -> None:
        # core/02 sections 21.1 and 21.13 say "Typical location", not "Location".
        for name in ("dashboard", "resource"):
            spec = type_spec(name)
            assert spec is not None
            assert not spec.location_is_binding, name

    def test_locations_marked_canonical_are_binding(self) -> None:
        for name in ("knowledge", "decision", "workspace", "person"):
            spec = type_spec(name)
            assert spec is not None
            assert spec.location_is_binding, name

    def test_the_workspace_manifest_location_is_its_own_kind(self) -> None:
        spec = type_spec("workspace")
        assert spec is not None
        assert [location.kind for location in spec.locations] == [LocationKind.WORKSPACE_MANIFEST]


class TestSystemManifest:
    """core/02 section 21.20, reconciling section 21 with core/05 section 6."""

    def test_the_vault_manifest_type_is_registered(self) -> None:
        spec = type_spec("system_manifest")
        assert spec is not None
        assert spec.required_fields == ()
        assert spec.recommended_authority == "authoritative"

    def test_its_location_is_binding(self) -> None:
        # The one document that identifies the vault must be where it says.
        spec = type_spec("system_manifest")
        assert spec is not None
        assert spec.location_is_binding

    def test_it_has_no_lifecycle(self) -> None:
        spec = type_spec("system_manifest")
        assert spec is not None
        assert spec.lifecycle_values == ()


class TestExceptsAndDismissals:
    """core/02 section 17.2 `excepts` and section 21.22 `finding_dismissal`.

    An exception is a standing rule a workspace carries, so it is a relation on
    the standard the workspace already has. A dismissal is a one-time judgement
    about one detected instance, so it is a record of its own.
    """

    def test_excepts_is_a_registered_relation(self) -> None:
        assert "excepts" in RELATION_TYPES

    def test_a_dismissal_is_a_registered_type(self) -> None:
        assert type_spec("finding_dismissal") is not None

    def test_it_lives_in_the_system_root(self) -> None:
        """Beside `skill_provenance`: vault-level bookkeeping, validated and indexed."""
        spec = type_spec("finding_dismissal")
        assert spec is not None
        assert spec.accepts(p("50_System/finding-dismissal_agents-md-statusline.md"))

    def test_it_does_not_belong_on_the_foreign_format_island(self) -> None:
        spec = type_spec("finding_dismissal")
        assert spec is not None
        assert not spec.accepts(p("50_System/Skills/a-skill/finding-dismissal_x.md"))

    def test_it_requires_the_code_the_fingerprint_and_a_reason(self) -> None:
        spec = type_spec("finding_dismissal")
        assert spec is not None
        assert set(spec.required_fields) == {"finding_code", "fingerprint", "reason"}

    def test_the_finding_path_is_registered_but_optional(self) -> None:
        """Prefixed: a bare `path` in frontmatter reads as the document's own."""
        spec = type_spec("finding_dismissal")
        assert spec is not None
        assert "finding_path" in spec.extra_fields
        assert "finding_path" not in spec.required_fields

    def test_a_dismissal_is_informational(self) -> None:
        spec = type_spec("finding_dismissal")
        assert spec is not None
        assert spec.recommended_authority == "informational"


class TestControlledVocabularies:
    def test_status_uses_the_okf_values(self) -> None:
        # core/02 section 10 preserves OKF v0.2 semantics exactly.
        assert STATUS_VALUES == ("draft", "stable", "deprecated")

    def test_authority_has_four_core_values(self) -> None:
        assert AUTHORITY_VALUES == (
            "authoritative",
            "informational",
            "provisional",
            "derived",
        )

    def test_the_core_relation_vocabulary_matches_section_17_2(self) -> None:
        # The registry is open (section 17.3) but registration is deliberate,
        # so this list only grows when section 17.2 does.
        assert RELATION_TYPES == (
            "related_to",
            "depends_on",
            "blocks",
            "implements",
            "supports",
            "applies_to",
            "superseded_by",
            "closes",
            "excepts",
        )

    def test_priority_has_four_values(self) -> None:
        assert PRIORITY_VALUES == ("low", "normal", "high", "critical")

    def test_there_is_no_entity_type_registry(self) -> None:
        # `entity` is retired (core/02 section 21.6), and `entity_type` was
        # its only required field. core/02 section 22 lists five controlled
        # registries.
        import never4ga.schema as schema

        assert not hasattr(schema, "ENTITY_TYPE_VALUES")

    def test_the_workspace_type_registry_matches_core_03(self) -> None:
        assert WORKSPACE_TYPE_VALUES == (
            "organization",
            "product",
            "project",
            "initiative",
            "research",
            "event",
            "personal_project",
            "ministry",
            "operations",
        )

    def test_no_numeric_confidence_vocabulary_exists(self) -> None:
        # core/02 section 33: v0.1 intentionally defines no generic confidence.
        import never4ga.schema as schema_package

        assert not [name for name in dir(schema_package) if "confidence" in name.lower()]


class TestPerson:
    """`person` is a concept type, not an `entity_type` (core/02 section 21.23).

    An entity record would be a knowledge note in a second place: traversal,
    aliases and retrieval are each something any document already has. A
    person is the exception. Nobody writes a *knowledge note* about a person,
    so people need a home, and `20_Life/` describes itself as the place for
    "people, family, community and social connections".
    """

    def test_a_person_belongs_in_a_life_area(self) -> None:
        spec = type_spec("person")
        assert spec is not None
        assert spec.accepts(p("20_Life/Relationships/jordan.md"))
        assert spec.accepts(p("20_Life/Community/sam-rivera.md"))

    def test_a_person_is_not_at_home_in_knowledge_notes(self) -> None:
        # A subject is written up in 30_Knowledge; a person is not a subject.
        spec = type_spec("person")
        assert spec is not None
        assert not spec.accepts(p("30_Knowledge/Notes/jordan.md"))

    def test_a_person_needs_no_workspace(self) -> None:
        # A person is not scoped to a project. One person can be a client's
        # stakeholder, a lead for one product and a member of a local club,
        # and none of those owns them.
        spec = type_spec("person")
        assert spec is not None
        assert "workspace" not in spec.required_fields


class TestDocumentationIsNotOnlyOurs:
    """`documentation` covers anything the vault holds, not only Never4gA.

    core/02 section 21.18. Without it a workspace wanting a README-equivalent
    would have to borrow a type that means something else: `context`,
    `knowledge` or `resource`. No other type is confined to one project.
    `50_System/Documentation/` is still where Never4gA documents itself,
    because that is where it sits, not because the type forbids anywhere else.
    """

    def test_a_workspace_may_hold_its_own_documentation(self) -> None:
        spec = type_spec("documentation")
        assert spec is not None
        assert spec.accepts(p("10_Workspaces/BezaCore-Labs/Workspaces/Wren/Documentation/api.md"))

    def test_a_life_area_may_hold_its_own(self) -> None:
        spec = type_spec("documentation")
        assert spec is not None
        assert spec.accepts(p("20_Life/Education/Documentation/how-grading-works.md"))

    def test_never4ga_still_documents_itself_where_it_did(self) -> None:
        """The control: Never4gA's own documentation is accepted where it sits."""
        spec = type_spec("documentation")
        assert spec is not None
        assert spec.accepts(p("50_System/Documentation/vault-layout.md"))
        assert spec.accepts(
            p(
                "10_Workspaces/BezaCore-Labs/Workspaces/Never4gA/Architecture/core/02-formal-schema.md"
            )
        )

    def test_it_requires_no_scope_because_one_of_its_homes_has_none(self) -> None:
        """`50_System/Documentation/` is not in a workspace.

        Creation fills `workspace` in from placement wherever placement implies
        one, as it does for `resource`.
        """
        spec = type_spec("documentation")
        assert spec is not None
        assert "workspace" not in spec.required_fields


class TestARunbookIsItsOwnType:
    """A `runbook` says how to operate a thing (core/02 section 21.26).

    Start it, stop it, recover it, what to do when it breaks, the sequence to
    run. No design, governance or reference type covers that.

    It is separate from `documentation` because retrieval has to tell them
    apart. When something is broken, "what do I do when this breaks" is a
    different need from "how is this built", and a type is how the system
    knows which it is holding.
    """

    def test_a_workspace_may_hold_runbooks(self) -> None:
        spec = type_spec("runbook")
        assert spec is not None
        assert spec.accepts(
            p("10_Workspaces/BezaCore-Labs/Workspaces/Acme-Infrastructure/Runbooks/restore.md")
        )

    def test_a_life_area_may_hold_one(self) -> None:
        """Operating a thing is not only a software concern."""
        spec = type_spec("runbook")
        assert spec is not None
        assert spec.accepts(p("20_Life/Home/Runbooks/restart-the-router.md"))

    def test_it_names_the_scope_it_belongs_to(self) -> None:
        spec = type_spec("runbook")
        assert spec is not None
        assert "workspace" in spec.required_fields

    def test_it_is_not_documentation_and_documentation_is_not_it(self) -> None:
        """The distinction is the point, so neither may absorb the other."""
        documentation = type_spec("documentation")
        runbook = type_spec("runbook")
        assert documentation is not None and runbook is not None
        assert not documentation.accepts(p("10_Workspaces/Wren/Runbooks/deploy.md"))
        assert not runbook.accepts(p("10_Workspaces/Wren/Documentation/architecture.md"))
