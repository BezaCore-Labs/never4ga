"""The draft state a tracker write passes through before anything is sent.

core/03 section 22: Never4gA MUST distinguish read operations, draft/proposed
writes, and actual writes. A proposal carries the target, the fields, the
lockVersion it was computed against, and a rendering a person can read.
Rendering makes no request.
"""

from __future__ import annotations

import pytest

from never4ga.domain.identity import ExternalId
from never4ga.ports.work_management import ProposedMutation, WriteAction

REF = ExternalId(provider="openproject", value="838")


class TestUpdate:
    def test_carries_the_target_the_fields_and_the_version(self) -> None:
        proposal = ProposedMutation.update(
            REF,
            fields={"status": "Closed"},
            current={"status": "New"},
            lock_version=3,
        )
        assert proposal.action is WriteAction.UPDATE
        assert proposal.ref == REF
        assert proposal.fields == {"status": "Closed"}
        assert proposal.lock_version == 3

    def test_changes_pair_before_with_after(self) -> None:
        proposal = ProposedMutation.update(
            REF,
            fields={"status": "Closed", "assignee": "ada"},
            current={"status": "New", "assignee": None},
            lock_version=3,
        )
        assert proposal.changes == {
            "status": ("New", "Closed"),
            "assignee": (None, "ada"),
        }

    def test_a_field_already_at_the_asked_value_is_not_a_change(self) -> None:
        proposal = ProposedMutation.update(
            REF,
            fields={"status": "Closed", "priority": "Normal"},
            current={"status": "New", "priority": "Normal"},
            lock_version=3,
        )
        assert set(proposal.changes) == {"status"}

    def test_an_update_that_changes_nothing_says_so(self) -> None:
        # Worth knowing before a request is made: a write that would change
        # nothing still bumps a journal on the tracker.
        proposal = ProposedMutation.update(
            REF, fields={"status": "New"}, current={"status": "New"}, lock_version=3
        )
        assert proposal.is_noop is True
        assert "nothing would change" in proposal.render()

    def test_requires_the_version_it_was_computed_against(self) -> None:
        # A proposal without one could not be applied safely: the API refuses a
        # PATCH without a fresh lockVersion, and a proposal that omitted it
        # would be a draft of something that cannot happen.
        with pytest.raises(ValueError, match="lock_version"):
            ProposedMutation.update(REF, fields={"status": "Closed"}, lock_version=None)

    def test_requires_at_least_one_field(self) -> None:
        with pytest.raises(ValueError, match="field"):
            ProposedMutation.update(REF, fields={}, lock_version=3)

    def test_renders_the_change_the_version_and_the_target(self) -> None:
        rendered = ProposedMutation.update(
            REF,
            fields={"status": "Closed", "assignee": "ada"},
            current={"status": "New", "assignee": None},
            lock_version=3,
        ).render()
        assert rendered.startswith("update openproject:838")
        assert "status" in rendered
        assert "New -> Closed" in rendered
        # An absent value reads as absent rather than as the word None.
        assert "(unset) -> ada" in rendered
        assert "lockVersion 3" in rendered


class TestCreate:
    def test_carries_the_project_and_the_fields(self) -> None:
        proposal = ProposedMutation.create("never4ga", fields={"subject": "Write the thing"})
        assert proposal.action is WriteAction.CREATE
        assert proposal.project == "never4ga"
        assert proposal.ref is None

    def test_has_no_version_because_there_is_nothing_to_lock(self) -> None:
        assert ProposedMutation.create("never4ga", fields={"subject": "x"}).lock_version is None

    def test_is_never_a_no_op(self) -> None:
        # There is no "already like that" for something that does not exist.
        assert ProposedMutation.create("never4ga", fields={"subject": "x"}).is_noop is False

    def test_requires_a_project(self) -> None:
        with pytest.raises(ValueError, match="project"):
            ProposedMutation.create("  ", fields={"subject": "x"})

    def test_requires_at_least_one_field(self) -> None:
        with pytest.raises(ValueError, match="field"):
            ProposedMutation.create("never4ga", fields={})

    def test_renders_the_project_and_every_field(self) -> None:
        rendered = ProposedMutation.create(
            "never4ga", fields={"subject": "Write the thing", "type": "Task"}
        ).render()
        assert rendered.startswith("create a work item in never4ga")
        assert "Write the thing" in rendered
        assert "Task" in rendered
        # Nothing is being replaced, so nothing should read as a replacement.
        assert "->" not in rendered


class TestComment:
    def test_carries_the_target_and_the_body(self) -> None:
        proposal = ProposedMutation.comment(REF, "why this closed")
        assert proposal.action is WriteAction.COMMENT
        assert proposal.ref == REF
        assert proposal.body == "why this closed"

    def test_has_no_version(self) -> None:
        # A comment is its own request and carries no lockVersion, unlike the
        # PATCH that a field change needs.
        assert ProposedMutation.comment(REF, "text").lock_version is None

    def test_is_never_a_no_op(self) -> None:
        assert ProposedMutation.comment(REF, "text").is_noop is False

    def test_requires_a_body(self) -> None:
        with pytest.raises(ValueError, match="body"):
            ProposedMutation.comment(REF, "   ")

    def test_renders_the_target_and_the_text(self) -> None:
        rendered = ProposedMutation.comment(REF, "why this closed").render()
        assert rendered.startswith("comment on openproject:838")
        assert "why this closed" in rendered


class TestValue:
    def test_is_frozen(self) -> None:
        proposal = ProposedMutation.comment(REF, "text")
        with pytest.raises(AttributeError):
            proposal.body = "other"  # type: ignore[misc]

    def test_fields_cannot_be_mutated_through_the_mapping_that_built_it(self) -> None:
        fields = {"status": "Closed"}
        proposal = ProposedMutation.update(REF, fields=fields, lock_version=1)
        fields["status"] = "Rejected"
        assert proposal.fields["status"] == "Closed"
        with pytest.raises(TypeError):
            proposal.fields["status"] = "Rejected"  # type: ignore[index]

    def test_rendering_is_pure(self) -> None:
        # Nothing in this step sends anything. Rendering the same proposal
        # twice must produce the same text and touch nothing.
        proposal = ProposedMutation.update(
            REF, fields={"status": "Closed"}, current={"status": "New"}, lock_version=3
        )
        assert proposal.render() == proposal.render()
