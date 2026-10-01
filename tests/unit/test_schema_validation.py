"""Schema v0.1 validation (core/02, especially the acceptance tests of section 35).

Validation reports; it never repairs. core/02 section 23 is explicit that repair
must be an explicit human act, and section 31 that a document stays readable even
when validation fails.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.layout import DocumentRole
from never4ga.schema import (
    SCHEMA_VERSION,
    Severity,
    ValidationLevel,
    validate_document,
)

WORKSPACE_ID = "0198d741-0cf9-7360-b426-964081ed0a39"
OTHER_ID = "0198d71c-f0ad-71bd-949a-9276f9ce4e84"


def p(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


def concept(**overrides: Any) -> dict[str, Any]:
    """A minimally valid knowledge concept (core/02 section 36)."""
    frontmatter: dict[str, Any] = {
        "type": "knowledge",
        "id": "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31",
        "schema": SCHEMA_VERSION,
        "title": "Example",
        "created_at": "2026-08-22T19:00:00Z",
    }
    frontmatter.update(overrides)
    return {key: value for key, value in frontmatter.items() if value is not None}


def codes(report: Any, severity: Severity | None = None) -> set[str]:
    return {issue.code for issue in report.issues if severity is None or issue.severity is severity}


DEFAULT_PATH = p("30_Knowledge/Notes/Example.md")


def check(
    frontmatter: dict[str, Any] | None,
    path: VaultPath = DEFAULT_PATH,
    level: ValidationLevel = ValidationLevel.STRICT,
    **kwargs: Any,
) -> Any:
    return validate_document(path, frontmatter, level=level, **kwargs)


class TestMinimalConcept:
    def test_the_canonical_minimal_example_validates(self) -> None:
        report = check(concept())
        assert report.ok, report.issues

    def test_the_recommended_durable_example_validates(self) -> None:
        report = check(
            concept(
                description="One-sentence description optimized for humans and retrieval.",
                generated={"by": "human:owner", "at": "2026-08-22T19:00:00Z"},
                status="stable",
                authority="informational",
                domains=[],
                tags=[],
                aliases=[],
            )
        )
        assert report.ok, report.issues

    def test_a_report_names_the_document_it_describes(self) -> None:
        assert check(concept()).path == DEFAULT_PATH


class TestRequiredFields:
    @pytest.mark.parametrize("field", ["type", "id", "schema", "title", "created_at"])
    def test_a_missing_required_field_is_an_error(self, field: str) -> None:
        frontmatter = concept()
        del frontmatter[field]
        report = check(frontmatter)
        assert not report.ok
        assert "missing_required_field" in codes(report, Severity.ERROR)
        assert any(issue.field == field for issue in report.issues)

    def test_a_document_with_no_frontmatter_at_all_is_an_error(self) -> None:
        report = check(None)
        assert not report.ok
        assert "missing_frontmatter" in codes(report, Severity.ERROR)

    def test_an_empty_frontmatter_block_reports_every_missing_field(self) -> None:
        report = check({})
        missing = [issue.field for issue in report.issues if issue.code == "missing_required_field"]
        assert set(missing) == {"type", "id", "schema", "title", "created_at"}

    def test_required_fields_are_checked_at_core_level(self) -> None:
        frontmatter = concept()
        del frontmatter["title"]
        assert not check(frontmatter, level=ValidationLevel.CORE).ok


class TestIdentity:
    """core/02 section 35, "Identity"."""

    def test_a_canonical_uuidv7_is_accepted(self) -> None:
        assert check(concept(id=str(ConceptId.new()))).ok

    def test_a_uuid4_is_rejected(self) -> None:
        report = check(concept(id="9f1c8a52-6d3e-4b21-9f8a-2c5d7e1b4a90"))
        assert "invalid_id" in codes(report, Severity.ERROR)

    def test_an_uppercase_uuid_is_rejected(self) -> None:
        # core/02 section 5.1 requires canonical lowercase hyphenated form.
        report = check(concept(id="0198D6F2-4CB1-7A2A-8B4A-1D72DDAB8F31"))
        assert "invalid_id" in codes(report, Severity.ERROR)

    def test_a_backend_row_id_is_rejected(self) -> None:
        # core/06 section 3: a backend key is never canonical identity.
        report = check(concept(id=42))
        assert "invalid_id" in codes(report, Severity.ERROR)

    def test_identity_does_not_depend_on_the_path(self) -> None:
        # Moving a concept changes nothing about its validity.
        first = check(concept(), path=p("30_Knowledge/Notes/A.md"))
        second = check(concept(), path=p("30_Knowledge/Notes/B.md"))
        assert first.ok and second.ok


class TestSchemaField:
    def test_the_declared_schema_version_is_accepted(self) -> None:
        assert check(concept()).ok

    def test_a_future_schema_version_warns_rather_than_fails(self) -> None:
        # core/02 section 31: warn rather than fail when best-effort is safe.
        report = check(concept(schema="never4ga/0.2"))
        assert report.ok
        assert "unrecognised_schema" in codes(report, Severity.WARNING)

    def test_a_foreign_schema_identifier_warns(self) -> None:
        report = check(concept(schema="someone-else/1.0"))
        assert "unrecognised_schema" in codes(report, Severity.WARNING)


class TestTimestamps:
    """core/02 section 7.4: ISO 8601 with an explicit offset."""

    @pytest.mark.parametrize(
        "value",
        ["2026-08-22T19:00:00Z", "2026-08-22T19:00:00+00:00", "2026-08-22T14:00:00-05:00"],
    )
    def test_an_offset_bearing_timestamp_is_accepted(self, value: str) -> None:
        assert check(concept(created_at=value)).ok

    @pytest.mark.parametrize("value", ["2026-08-22T19:00:00", "2026-08-22", "yesterday", ""])
    def test_a_timestamp_without_an_explicit_offset_is_an_error(self, value: str) -> None:
        report = check(concept(created_at=value))
        assert "invalid_timestamp" in codes(report, Severity.ERROR)

    def test_a_non_string_timestamp_is_an_error(self) -> None:
        # A YAML 1.1 reader elsewhere may have handed us a date object.
        report = check(concept(created_at=20260822))
        assert "invalid_timestamp" in codes(report, Severity.ERROR)

    def test_stale_after_must_also_carry_an_offset(self) -> None:
        report = check(concept(stale_after="2026-11-22"))
        assert "invalid_timestamp" in codes(report, Severity.ERROR)

    def test_occurred_at_is_validated_as_a_timestamp(self) -> None:
        report = check(
            concept(type="activity_log", workspace=WORKSPACE_ID, occurred_at="not a time"),
            path=p("10_Workspaces/BezaCore/Logs/session.md"),
        )
        assert "invalid_timestamp" in codes(report, Severity.ERROR)

    @pytest.mark.parametrize("field", ["target_date", "due_date"])
    def test_a_plain_date_field_accepts_a_bare_date(self, field: str) -> None:
        # core/02 sections 21.8 and 21.10 spell these "YYYY-MM-DD".
        report = check(
            concept(type="task", workspace=WORKSPACE_ID, lifecycle="todo", **{field: "2026-09-01"}),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
        )
        assert "invalid_date" not in codes(report)

    def test_a_malformed_date_field_is_an_error(self) -> None:
        report = check(
            concept(
                type="task", workspace=WORKSPACE_ID, lifecycle="todo", due_date="the 1st of Sept"
            ),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
        )
        assert "invalid_date" in codes(report, Severity.ERROR)


class TestUnknownTypes:
    def test_an_unknown_type_stays_readable(self) -> None:
        # core/02 section 5.1 and section 35, "Extensibility".
        report = check(concept(type="some_future_type"))
        assert report.ok
        assert "unregistered_type" in codes(report, Severity.WARNING)

    def test_an_unknown_type_is_not_even_warned_about_below_strict(self) -> None:
        report = check(concept(type="some_future_type"), level=ValidationLevel.CORE)
        assert "unregistered_type" not in codes(report)

    def test_a_type_that_is_not_snake_case_is_an_error(self) -> None:
        report = check(concept(type="Some Future Type"))
        assert "invalid_type" in codes(report, Severity.ERROR)

    def test_an_unknown_type_has_no_placement_opinion(self) -> None:
        # Never4gA cannot know where an unregistered type belongs.
        report = check(concept(type="some_future_type"), path=p("00_Inbox/thing.md"))
        assert "misplaced_document" not in codes(report)


class TestPlacement:
    """core/02 sections 23 and 35, "Type placement"."""

    def test_a_knowledge_note_in_notes_is_valid(self) -> None:
        assert check(concept()).ok

    def test_a_decision_in_notes_is_a_placement_error(self) -> None:
        report = check(
            concept(type="decision", workspace=WORKSPACE_ID, lifecycle="accepted"),
            path=p("30_Knowledge/Notes/d.md"),
        )
        assert "misplaced_document" in codes(report, Severity.ERROR)

    def test_a_knowledge_note_in_a_decisions_directory_is_a_placement_error(self) -> None:
        report = check(concept(), path=p("10_Workspaces/BezaCore/Decisions/x.md"))
        assert "misplaced_document" in codes(report, Severity.ERROR)

    def test_documentation_may_live_in_a_workspace_architecture_folder(self) -> None:
        # core/03 section 10: the software profile's Architecture/ holds
        # "design docs and technical structure". A product's specifications
        # are exactly that, and core/02 section 21.18 lists it as a home.
        report = check(
            concept(type="documentation", workspace=WORKSPACE_ID),
            path=p("10_Workspaces/BezaCore/Workspaces/Never4gA/Architecture/core/01-vault.md"),
        )
        assert "misplaced_document" not in codes(report)

    def test_documentation_in_system_documentation_is_still_valid(self) -> None:
        report = check(concept(type="documentation"), path=p("50_System/Documentation/x.md"))
        assert "misplaced_document" not in codes(report)

    # core/03 sections 9 and 10 define eight profile extension folders, and
    # the Type Registry gives each of them types that may be placed there.
    # Otherwise they would be folders nothing could legally be placed in.

    @pytest.mark.parametrize(
        "section",
        ["Strategy", "Brand", "Finance", "Operations", "Requirements", "Testing"],
    )
    def test_a_standard_may_live_in_a_profile_section_that_prescribes(self, section: str) -> None:
        report = check(
            concept(type="standard", authority="authoritative"),
            path=p(f"10_Workspaces/BezaCore/{section}/palette.md"),
        )
        assert "misplaced_document" not in codes(report)

    def test_a_vault_wide_standard_is_still_valid(self) -> None:
        report = check(concept(type="standard"), path=p("50_System/Standards/naming.md"))
        assert "misplaced_document" not in codes(report)

    @pytest.mark.parametrize(
        "section",
        [
            "Strategy",
            "Brand",
            "Finance",
            "Operations",
            "Requirements",
            "Testing",
            "Releases",
        ],
    )
    def test_a_resource_may_live_in_any_profile_section(self, section: str) -> None:
        # Supporting material appears wherever the material it supports does.
        report = check(
            concept(type="resource"),
            path=p(f"10_Workspaces/BezaCore/{section}/sample-report.md"),
        )
        assert "misplaced_document" not in codes(report)

    def test_a_plan_may_live_in_releases(self) -> None:
        # core/03 section 10 defines Releases/ as "release plans" first.
        report = check(
            concept(type="plan", workspace=WORKSPACE_ID, lifecycle="active"),
            path=p("10_Workspaces/BezaCore/Releases/v1.md"),
        )
        assert "misplaced_document" not in codes(report)

    def test_a_profile_section_does_not_accept_every_type(self) -> None:
        # A profile section accepts some types, not every type.
        report = check(
            concept(type="decision", workspace=WORKSPACE_ID, lifecycle="accepted"),
            path=p("10_Workspaces/BezaCore/Brand/d.md"),
        )
        assert "misplaced_document" in codes(report, Severity.ERROR)

    def test_placement_is_only_a_warning_below_strict(self) -> None:
        # core/02 section 2.3 has `core` check placement; section 23 calls a
        # mismatch a strict-validation error. Reporting it at both levels with
        # different severity satisfies each without inventing a third rule.
        report = check(
            concept(), path=p("10_Workspaces/BezaCore/Decisions/x.md"), level=ValidationLevel.CORE
        )
        assert "misplaced_document" in codes(report, Severity.WARNING)
        assert report.ok

    def test_the_inbox_is_exempt_because_it_holds_unprocessed_material(self) -> None:
        # core/01 section 5: Inbox is temporary/unprocessed by definition.
        report = check(
            concept(type="decision", workspace=WORKSPACE_ID, lifecycle="proposed"),
            path=p("00_Inbox/captured.md"),
        )
        assert "misplaced_document" not in codes(report)

    def test_an_advisory_location_downgrades_to_a_warning(self) -> None:
        report = check(
            concept(type="resource", workspace=WORKSPACE_ID),
            path=p("30_Knowledge/Notes/spec.md"),
        )
        assert "misplaced_document" in codes(report, Severity.WARNING)

    def test_knowledge_notes_must_stay_flat(self) -> None:
        # core/01 section 8: no long-lived topical folder hierarchy.
        report = check(concept(), path=p("30_Knowledge/Notes/Programming/Python/thing.md"))
        assert "nested_flat_area" in codes(report, Severity.ERROR)

    def test_a_person_sits_directly_in_an_area(self) -> None:
        # core/02 section 21.23: people live in `20_Life/<Area>/`, and the area
        # groups by responsibility rather than by what kind of person somebody
        # is.
        assert check(concept(type="person"), path=p("20_Life/Relationships/Ada Lovelace.md")).ok

    def test_a_child_workspace_manifest_is_correctly_placed(self) -> None:
        assert check(
            concept(
                type="workspace",
                workspace_type="product",
                lifecycle="active",
                parent=WORKSPACE_ID,
            ),
            path=p("10_Workspaces/BezaCore/Workspaces/Sparrow/workspace.md"),
        ).ok

    def test_a_child_workspace_outside_the_workspaces_directory_is_rejected(self) -> None:
        # core/01 section 6: all child workspaces live under Workspaces/.
        report = check(
            concept(type="workspace", workspace_type="product", lifecycle="active"),
            path=p("10_Workspaces/BezaCore/Sparrow/workspace.md"),
        )
        assert "misplaced_document" in codes(report, Severity.ERROR)


class TestReservedDocuments:
    """core/02 sections 3.2 and 4, and section 35, "OKF compatibility"."""

    def test_a_directory_index_must_not_carry_concept_frontmatter(self) -> None:
        report = check(concept(), path=p("30_Knowledge/index.md"))
        assert "reserved_document_has_frontmatter" in codes(report, Severity.ERROR)

    def test_a_directory_index_with_no_frontmatter_is_fine(self) -> None:
        assert check(None, path=p("30_Knowledge/index.md")).ok

    def test_the_root_index_may_declare_the_okf_version(self) -> None:
        assert check({"okf_version": "0.2"}, path=p("index.md")).ok

    def test_the_root_index_must_not_be_a_concept(self) -> None:
        report = check(concept(), path=p("index.md"))
        assert "reserved_document_has_frontmatter" in codes(report, Severity.ERROR)

    def test_log_md_must_not_be_an_activity_log_concept(self) -> None:
        # core/01 section 13 and core/02 section 21.14.
        report = check(
            concept(
                type="activity_log", workspace=WORKSPACE_ID, occurred_at="2026-08-22T19:00:00Z"
            ),
            path=p("10_Workspaces/BezaCore/Logs/log.md"),
        )
        assert "reserved_document_has_frontmatter" in codes(report, Severity.ERROR)

    def test_an_activity_log_under_another_name_is_fine(self) -> None:
        assert check(
            concept(
                type="activity_log", workspace=WORKSPACE_ID, occurred_at="2026-08-22T19:00:00Z"
            ),
            path=p("10_Workspaces/BezaCore/Logs/2026-08-22 kickoff.md"),
        ).ok

    def test_the_role_can_be_supplied_by_the_caller(self) -> None:
        report = check(concept(), path=p("30_Knowledge/Notes/x.md"), role=DocumentRole.CONCEPT)
        assert report.ok


class TestLifecycleAndStatus:
    """core/02 sections 10, 11 and 35, "Lifecycle"."""

    def test_status_stable_with_lifecycle_paused_is_valid(self) -> None:
        assert check(
            concept(
                type="workspace",
                workspace_type="product",
                status="stable",
                lifecycle="paused",
            ),
            path=p("10_Workspaces/BezaCore/workspace.md"),
        ).ok

    def test_an_unknown_status_is_an_error(self) -> None:
        report = check(concept(status="in_progress"))
        assert "invalid_status" in codes(report, Severity.ERROR)

    def test_status_is_not_reusable_for_task_completion(self) -> None:
        report = check(
            concept(type="task", workspace=WORKSPACE_ID, lifecycle="todo", status="done"),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
        )
        assert "invalid_status" in codes(report, Severity.ERROR)

    def test_a_task_uses_task_lifecycle_vocabulary(self) -> None:
        assert check(
            concept(type="task", workspace=WORKSPACE_ID, lifecycle="in_progress"),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
        ).ok

    def test_a_task_may_not_use_decision_lifecycle_vocabulary(self) -> None:
        report = check(
            concept(type="task", workspace=WORKSPACE_ID, lifecycle="accepted"),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
        )
        assert "invalid_lifecycle" in codes(report, Severity.ERROR)

    def test_an_unknown_lifecycle_is_tolerated_below_strict(self) -> None:
        # core/02 section 11: tolerated at base validation, rejected at strict.
        report = check(
            concept(type="task", workspace=WORKSPACE_ID, lifecycle="invented"),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
            level=ValidationLevel.CORE,
        )
        assert "invalid_lifecycle" not in codes(report)

    def test_a_missing_required_lifecycle_is_an_error(self) -> None:
        report = check(
            concept(type="task", workspace=WORKSPACE_ID),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
        )
        assert "missing_required_field" in codes(report, Severity.ERROR)

    def test_an_unregistered_type_lifecycle_is_not_second_guessed(self) -> None:
        report = check(concept(type="some_future_type", lifecycle="whatever"))
        assert "invalid_lifecycle" not in codes(report)


class TestAuthorityAndPriority:
    def test_the_four_authority_values_are_accepted(self) -> None:
        for value in ("authoritative", "informational", "provisional", "derived"):
            assert check(concept(authority=value)).ok, value

    def test_an_unknown_authority_is_an_error(self) -> None:
        report = check(concept(authority="probably_true"))
        assert "invalid_authority" in codes(report, Severity.ERROR)

    def test_an_unknown_priority_is_an_error(self) -> None:
        report = check(
            concept(type="task", workspace=WORKSPACE_ID, lifecycle="todo", priority="urgent"),
            path=p("10_Workspaces/BezaCore/Tasks/t.md"),
        )
        assert "invalid_priority" in codes(report, Severity.ERROR)


class TestProvenance:
    """core/02 sections 13, 14 and 35, "Provenance"."""

    def test_an_agent_producer_is_recorded_intact(self) -> None:
        report = check(
            concept(generated={"by": "claude-code/claude-sonnet", "at": "2026-08-22T19:00:00Z"})
        )
        assert report.ok

    @pytest.mark.parametrize(
        "actor", ["human:owner", "codex/gpt-5", "process:importer", "claude-code/claude-sonnet"]
    )
    def test_the_okf_actor_forms_are_accepted(self, actor: str) -> None:
        assert check(concept(generated={"by": actor, "at": "2026-08-22T19:00:00Z"})).ok, actor

    def test_an_actor_with_no_namespace_is_an_error(self) -> None:
        report = check(concept(generated={"by": "ada", "at": "2026-08-22T19:00:00Z"}))
        assert "invalid_actor" in codes(report, Severity.ERROR)

    def test_generated_requires_both_by_and_at(self) -> None:
        report = check(concept(generated={"by": "human:owner"}))
        assert "incomplete_generated" in codes(report, Severity.ERROR)

    def test_generated_at_must_be_a_real_timestamp(self) -> None:
        report = check(concept(generated={"by": "human:owner", "at": "recently"}))
        assert "invalid_timestamp" in codes(report, Severity.ERROR)

    def test_a_source_without_a_resource_fails(self) -> None:
        report = check(concept(sources=[{"id": "x", "title": "No resource here"}]))
        assert "source_missing_resource" in codes(report, Severity.ERROR)

    def test_a_complete_source_entry_is_accepted(self) -> None:
        assert check(
            concept(
                sources=[
                    {
                        "id": "sqlite-fts5",
                        "resource": "https://sqlite.org/fts5.html",
                        "title": "SQLite FTS5 Documentation",
                    }
                ]
            )
        ).ok

    def test_human_verification_does_not_erase_the_original_producer(self) -> None:
        frontmatter = concept(
            generated={"by": "codex/gpt-5", "at": "2026-08-22T19:00:00Z"},
            verified=[{"by": "human:owner", "at": "2026-08-22T20:00:00Z"}],
        )
        report = check(frontmatter)
        assert report.ok
        assert frontmatter["generated"]["by"] == "codex/gpt-5"

    def test_a_verifier_must_also_be_a_well_formed_actor(self) -> None:
        report = check(concept(verified=[{"by": "someone", "at": "2026-08-22T20:00:00Z"}]))
        assert "invalid_actor" in codes(report, Severity.ERROR)


class TestRelations:
    """core/02 sections 17 and 35, "Relationships"."""

    def test_a_registered_relation_with_a_uuid_target_is_valid(self) -> None:
        assert check(concept(relations=[{"type": "depends_on", "target": OTHER_ID}])).ok

    def test_a_relation_target_must_be_a_stable_uuid(self) -> None:
        report = check(concept(relations=[{"type": "depends_on", "target": "../Notes/Other.md"}]))
        assert "invalid_relation_target" in codes(report, Severity.ERROR)

    def test_a_relation_needs_both_type_and_target(self) -> None:
        report = check(concept(relations=[{"type": "depends_on"}]))
        assert "incomplete_relation" in codes(report, Severity.ERROR)

    def test_an_unknown_relation_type_is_preserved_and_warned_about(self) -> None:
        # core/02 section 17.3: consumers MUST preserve unknown relation types.
        report = check(concept(relations=[{"type": "rhymes_with", "target": OTHER_ID}]))
        assert report.ok
        assert "unregistered_relation_type" in codes(report, Severity.WARNING)

    def test_a_missing_target_is_reported_when_the_known_ids_are_supplied(self) -> None:
        report = check(
            concept(relations=[{"type": "depends_on", "target": OTHER_ID}]),
            known_ids={ConceptId.parse(WORKSPACE_ID)},
        )
        assert "unresolved_relation_target" in codes(report, Severity.ERROR)

    def test_a_resolvable_target_produces_no_issue(self) -> None:
        report = check(
            concept(relations=[{"type": "depends_on", "target": OTHER_ID}]),
            known_ids={ConceptId.parse(OTHER_ID)},
        )
        assert report.ok

    def test_targets_are_not_resolved_when_no_id_set_is_supplied(self) -> None:
        # A single-document validation cannot know what else exists.
        assert check(concept(relations=[{"type": "depends_on", "target": OTHER_ID}])).ok

    def test_relations_must_be_a_list_of_mappings(self) -> None:
        report = check(concept(relations="depends_on: something"))
        assert "invalid_relations" in codes(report, Severity.ERROR)


class TestScopeFields:
    """core/02 section 16."""

    def test_the_workspace_field_must_be_a_uuid(self) -> None:
        report = check(
            concept(type="goal", workspace="BezaCore", lifecycle="active"),
            path=p("10_Workspaces/BezaCore/Goals/g.md"),
        )
        assert "invalid_workspace_reference" in codes(report, Severity.ERROR)

    def test_the_parent_field_must_be_a_uuid(self) -> None:
        report = check(concept(parent="something"))
        assert "invalid_parent_reference" in codes(report, Severity.ERROR)

    def test_a_workspace_scoped_concept_outside_a_workspace_is_reported(self) -> None:
        report = check(
            concept(type="goal", workspace=WORKSPACE_ID, lifecycle="active"),
            path=p("30_Knowledge/Notes/g.md"),
        )
        assert "misplaced_document" in codes(report, Severity.ERROR)


AREA_ID: Final = "01a04f5f-5581-737b-80b2-16181bec2321"
AREA_PATH: Final = p("20_Life/Gardening/spring-planting-plan.md")


class TestTheAreaScopeField:
    """core/02 section 16.3."""

    def test_an_area_placed_document_requires_area(self) -> None:
        report = check(
            concept(type="research_note", lifecycle="active"),
            path=AREA_PATH,
        )
        assert "missing_required_field" in codes(report, Severity.ERROR)

    def test_supplying_it_satisfies_the_requirement(self) -> None:
        report = check(
            concept(type="research_note", area=AREA_ID, lifecycle="active"),
            path=AREA_PATH,
        )
        assert report.ok, [issue.code for issue in report.issues]

    def test_workspace_does_not_satisfy_it_in_an_area(self) -> None:
        """An area is not a workspace: it is carried, not pursued."""
        report = check(
            concept(type="research_note", workspace=WORKSPACE_ID, lifecycle="active"),
            path=AREA_PATH,
        )
        assert "missing_required_field" in codes(report, Severity.ERROR)

    def test_area_does_not_satisfy_the_requirement_in_a_workspace(self) -> None:
        report = check(
            concept(type="research_note", area=AREA_ID, lifecycle="active"),
            path=p("10_Workspaces/Acme/Research/r.md"),
        )
        assert "missing_required_field" in codes(report, Severity.ERROR)

    def test_the_area_field_must_be_a_uuid(self) -> None:
        report = check(
            concept(type="research_note", area="Gardening", lifecycle="active"),
            path=AREA_PATH,
        )
        assert "invalid_area_reference" in codes(report, Severity.ERROR)

    def test_it_is_a_registered_field_rather_than_an_unknown_one(self) -> None:
        report = check(
            concept(type="research_note", area=AREA_ID, lifecycle="active"),
            path=AREA_PATH,
        )
        assert "unregistered_field" not in codes(report)

    def test_carrying_both_scopes_is_a_contradiction(self) -> None:
        """A concept has one scope, so carrying both is an error, not a ranking."""
        report = check(
            concept(
                type="research_note",
                area=AREA_ID,
                workspace=WORKSPACE_ID,
                lifecycle="active",
            ),
            path=AREA_PATH,
        )
        assert "conflicting_scope_fields" in codes(report, Severity.ERROR)

    def test_an_area_placed_document_is_not_reported_as_misplaced(self) -> None:
        report = check(
            concept(type="research_note", area=AREA_ID, lifecycle="active"),
            path=p("20_Life/Reading/classics/war-and-peace/chapter-001.md"),
        )
        assert "misplaced_document" not in codes(report)

    def test_knowledge_in_an_area_is_still_misplaced(self) -> None:
        report = check(concept(type="knowledge"), path=AREA_PATH)
        assert "misplaced_document" in codes(report, Severity.ERROR)


class TestExtensibility:
    """core/02 sections 19, 20, 31 and 35, "Extensibility"."""

    def test_unknown_top_level_fields_are_accepted(self) -> None:
        report = check(concept(some_future_field="kept", another=[1, 2]))
        assert report.ok

    def test_unknown_top_level_fields_are_noted_at_strict_level(self) -> None:
        report = check(concept(some_future_field="kept"))
        assert "unregistered_field" in codes(report, Severity.WARNING)

    def test_a_connection_definitions_own_fields_are_registered(self) -> None:
        # An `integration` concept is the canonical half of a connection
        # (core/02 section 21.19), so its fields are registered. Otherwise every
        # correctly written connection reports four findings at strict level,
        # which trains a reader to ignore `doctor`.
        report = check(
            concept(
                type="integration",
                title="Work OpenProject",
                connection="work_openproject",
                provider="openproject",
                base_url="https://openproject.example",
                project_ref="never4ga",
            ),
            VaultPath.parse("50_System/Integrations/work-openproject.md"),
        )
        assert report.ok, report.issues
        assert "unregistered_field" not in codes(report)

    def test_a_workspace_manifests_cover_image_is_registered(self) -> None:
        # The active-workspaces cards read `cover` off the manifest, so
        # Never4gA both writes this field and depends on it. Unregistered, every
        # workspace would report `unregistered_field` at strict level, and a
        # warning nobody can clear teaches a reader to stop reading the ones
        # they can.
        report = check(
            concept(
                type="workspace",
                workspace_type="product",
                lifecycle="active",
                cover="Assets/cover.png",
            ),
            path=p("10_Workspaces/BezaCore/workspace.md"),
        )
        assert report.ok, report.issues
        assert "unregistered_field" not in codes(report)

    def test_cover_is_registered_for_the_workspace_type_only(self) -> None:
        # Registering it broadly would bless it on every concept in the vault.
        # core/02 section 21.2 names it on the workspace manifest and nowhere
        # else.
        report = check(concept(cover="Assets/cover.png"))
        assert "unregistered_field" in codes(report, Severity.WARNING)

    def test_a_token_shaped_field_is_still_refused(self) -> None:
        # Registering the portable half must not quietly bless the secret half.
        # core/03 section 16: those never enter the vault at all.
        report = check(
            concept(
                type="integration",
                title="Work OpenProject",
                connection="work_openproject",
                provider="openproject",
                api_token="hunter2",
            ),
            VaultPath.parse("50_System/Integrations/work-openproject.md"),
        )
        assert "unregistered_field" in codes(report, Severity.WARNING)

    def test_namespaced_extension_data_raises_no_warning(self) -> None:
        # core/02 section 20 is where producer-specific metadata SHOULD live.
        report = check(concept(extensions={"my_local_workflow": {"review_bucket": "weekly"}}))
        assert report.ok
        assert "unregistered_field" not in codes(report)

    def test_extensions_must_be_namespaced_mappings(self) -> None:
        report = check(concept(extensions={"review_bucket": "weekly"}))
        assert "invalid_extensions" in codes(report, Severity.ERROR)

    def test_an_unknown_profile_reduces_validation_rather_than_rejecting(self) -> None:
        # core/02 section 19.2.
        report = check(concept(profiles=["someone/unknown/0.1"]))
        assert report.ok
        assert "unregistered_profile" in codes(report, Severity.WARNING)

    def test_the_base_workspace_profile_is_recognised(self) -> None:
        report = check(
            concept(
                type="workspace",
                workspace_type="product",
                lifecycle="active",
                profiles=["never4ga/workspace/base/0.1"],
            ),
            path=p("10_Workspaces/BezaCore/workspace.md"),
        )
        assert "unregistered_profile" not in codes(report)

    def test_profiles_must_be_a_list_of_strings(self) -> None:
        report = check(concept(profiles="never4ga/workspace/base/0.1"))
        assert "invalid_profiles" in codes(report, Severity.ERROR)


class TestClassification:
    """core/02 section 9."""

    def test_an_unregistered_domain_is_a_strict_warning(self) -> None:
        report = check(concept(domains=["underwater_basket_weaving"]))
        assert report.ok
        assert "unregistered_domain" in codes(report, Severity.WARNING)

    def test_tags_are_free_form(self) -> None:
        report = check(concept(tags=["python", "retrieval"]))
        assert report.ok
        assert "unregistered_domain" not in codes(report)

    def test_a_tag_that_is_not_lowercase_is_a_warning(self) -> None:
        report = check(concept(tags=["Python"]))
        assert "unnormalised_tag" in codes(report, Severity.WARNING)

    @pytest.mark.parametrize("field", ["tags", "domains", "aliases"])
    def test_a_scalar_where_a_list_belongs_is_an_error(self, field: str) -> None:
        report = check(concept(**{field: "single"}))
        assert "invalid_list_field" in codes(report, Severity.ERROR)


class TestWorkspaceTypes:
    def test_a_registered_workspace_type_is_accepted(self) -> None:
        assert check(
            concept(type="workspace", workspace_type="product", lifecycle="active"),
            path=p("10_Workspaces/BezaCore/workspace.md"),
        ).ok

    def test_an_unregistered_workspace_type_warns(self) -> None:
        report = check(
            concept(type="workspace", workspace_type="side_quest", lifecycle="active"),
            path=p("10_Workspaces/BezaCore/workspace.md"),
        )
        assert "unregistered_workspace_type" in codes(report, Severity.WARNING)


class TestStaleness:
    """core/02 sections 15 and 35, "Staleness"."""

    def test_a_stale_concept_still_validates(self) -> None:
        # Stale content remains available and is flagged, never removed.
        report = check(concept(stale_after="2020-01-01T00:00:00Z"))
        assert report.ok


class TestValidationLevels:
    def test_okf_level_checks_only_okf_concerns(self) -> None:
        # No Never4gA id at all: OKF does not require one.
        frontmatter = {"type": "knowledge", "title": "Example"}
        assert check(frontmatter, level=ValidationLevel.OKF).ok

    def test_okf_level_still_requires_a_type(self) -> None:
        report = check({"title": "Example"}, level=ValidationLevel.OKF)
        assert "missing_required_field" in codes(report, Severity.ERROR)

    def test_okf_level_still_rejects_a_source_without_a_resource(self) -> None:
        frontmatter = {"type": "knowledge", "title": "x", "sources": [{"id": "a"}]}
        report = check(frontmatter, level=ValidationLevel.OKF)
        assert "source_missing_resource" in codes(report, Severity.ERROR)

    def test_core_level_requires_never4ga_identity(self) -> None:
        report = check({"type": "knowledge", "title": "Example"}, level=ValidationLevel.CORE)
        assert "missing_required_field" in codes(report, Severity.ERROR)

    def test_strict_adds_warnings_core_does_not_raise(self) -> None:
        frontmatter = concept(domains=["invented_domain"])
        assert codes(check(frontmatter, level=ValidationLevel.CORE), Severity.WARNING) == set()
        assert "unregistered_domain" in codes(
            check(frontmatter, level=ValidationLevel.STRICT), Severity.WARNING
        )


class TestIssueShape:
    """details/api-cli-mcp-contract.md section 12: structured, not English-only."""

    def test_every_issue_carries_a_stable_machine_code(self) -> None:
        report = check(concept(id="nope", authority="nope"))
        for issue in report.issues:
            assert issue.code == issue.code.lower()
            assert " " not in issue.code

    def test_an_issue_names_the_field_it_concerns(self) -> None:
        report = check(concept(authority="nope"))
        assert [issue.field for issue in report.issues] == ["authority"]

    def test_an_issue_offers_a_repair_hint(self) -> None:
        report = check(concept(authority="nope"))
        assert report.issues[0].repair_hint

    def test_errors_and_warnings_are_separable(self) -> None:
        report = check(concept(id="nope", domains=["invented"]))
        assert [issue.code for issue in report.errors] == ["invalid_id"]
        assert [issue.code for issue in report.warnings] == ["unregistered_domain"]

    def test_ok_means_no_errors_not_no_issues(self) -> None:
        report = check(concept(domains=["invented"]))
        assert report.ok
        assert report.issues


class TestForeignFormatDocuments:
    """core/02 sections 2.2 and 3.3: not concepts, not validated as concepts."""

    def test_a_template_is_not_validated_as_a_concept(self) -> None:
        template = {"type": "knowledge", "id": "{{id}}", "title": "{{title}}"}
        assert check(template, path=p("50_System/Templates/knowledge.md")).ok

    def test_a_skill_file_is_not_validated_as_a_concept(self) -> None:
        assert check(
            {"name": "tdd", "description": "..."},
            path=p("50_System/Skills/tdd/SKILL.md"),
        ).ok

    def test_an_obsidian_readme_is_not_validated_as_a_concept(self) -> None:
        assert check(None, path=p("50_System/Integrations/Obsidian/README.md")).ok

    def test_an_integration_concept_is_still_validated(self) -> None:
        report = check(concept(type="integration"), path=p("50_System/Integrations/Obsidian.md"))
        assert report.ok


class TestARequiredFieldIsRegisteredByDefinition:
    """A type's required fields count as registered at strict level.

    Otherwise a type would warn about its own mandatory frontmatter. Most
    types require only fields the base set already registers, such as
    `workspace` and `lifecycle`; `skill_provenance` requires fields of its own.
    """

    def test_a_types_required_field_does_not_warn(self) -> None:
        report = validate_document(
            VaultPath.parse("50_System/skill-provenance_x.md"),
            {
                "type": "skill_provenance",
                "id": "01a055a0-0820-732c-b3f6-342482f010bb",
                "schema": "never4ga/0.1",
                "title": "Vendored: x",
                "created_at": "2026-08-31T02:22:26Z",
                "skill_path": "50_System/Skills/x",
                "origin": {"kind": "local", "fetched_at": "2026-08-31T02:22:26Z"},
                "integrity": {"algorithm": "sha256", "tree_digest": "abc"},
            },
            level=ValidationLevel.STRICT,
        )
        unregistered = [f for f in report.issues if f.code == "unregistered_field"]
        assert not unregistered, [f.message for f in unregistered]

    def test_a_genuinely_unknown_field_still_warns(self) -> None:
        """The check must keep doing its job."""
        report = validate_document(
            VaultPath.parse("30_Knowledge/Notes/x.md"),
            {
                "type": "knowledge",
                "id": "01a055a0-0820-732c-b3f6-342482f010bb",
                "schema": "never4ga/0.1",
                "title": "x",
                "created_at": "2026-08-31T02:22:26Z",
                "something_nobody_registered": 1,
            },
            level=ValidationLevel.STRICT,
        )
        assert any(f.code == "unregistered_field" for f in report.issues)


class TestRequiredReading:
    """core/02 section 21.15: whether every startup reads a standard in full."""

    STANDARD_PATH = p("50_System/Standards/rule.md")

    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_valid_and_registered(self, value: bool) -> None:
        report = check(concept(type="standard", required_reading=value), path=self.STANDARD_PATH)
        assert "invalid_required_reading" not in codes(report)
        assert "unregistered_field" not in codes(report)

    @pytest.mark.parametrize("value", ["true", "yes", 1, None, ["true"]])
    def test_anything_else_is_an_error(self, value: object) -> None:
        frontmatter = concept(type="standard")
        frontmatter["required_reading"] = value
        report = check(frontmatter, path=self.STANDARD_PATH)
        assert "invalid_required_reading" in codes(report, Severity.ERROR)
