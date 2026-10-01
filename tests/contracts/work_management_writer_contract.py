"""WorkManagementWriter contract.

Specification:
- core/03 section 22 -- read, draft/proposed write, actual write are three
  distinct things, and provider concurrency/version mechanisms are honoured.
- core/03 section 24 -- "If a provider lacks a feature, the capability is
  reported unavailable." A writer is a separate role for exactly that reason:
  a provider that cannot write must not be made to implement writing. A writer
  is, however, always also a reader -- it needs the current version to propose
  against and the current state to verify with, so reading is a prerequisite of
  writing rather than a separate concern.
- details/openproject-adapter.md section 6 -- writes read current state first,
  use the current version, and return conflict errors rather than overwriting.
- core/06 section 22 -- one reusable contract suite; a new backend subclasses
  it rather than restating the behaviour.
"""

from __future__ import annotations

import pytest

from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ExternalId
from never4ga.errors import CapabilityNotSupportedError
from never4ga.ports.work_management import (
    ProposedMutation,
    WorkItem,
    WorkManagementWriter,
    WriteAction,
)


class WorkManagementWriterContract:
    """What every writer must do, whatever it writes to."""

    @pytest.fixture
    def writer(self) -> WorkManagementWriter:
        raise NotImplementedError("supply a WorkManagementWriter fixture")

    @pytest.fixture
    def known_item(self, writer: WorkManagementWriter) -> WorkItem:
        raise NotImplementedError("supply a work item the writer can reach")

    @pytest.fixture
    def project(self) -> str:
        raise NotImplementedError("supply a project reference the writer can create in")

    # -- proposing --------------------------------------------------------

    def test_declares_what_it_can_write(self, writer: WorkManagementWriter) -> None:
        assert WorkManagementCapability.UPDATE_WORK_ITEM in writer.capabilities

    def test_proposing_an_update_carries_the_current_version(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        # section 6: "use current lockVersion". A proposal that did not carry
        # one could not be applied safely, so it is part of the draft rather
        # than something found later.
        proposal = writer.propose_update(known_item.ref, {"status": "Closed"})
        assert proposal.action is WriteAction.UPDATE
        assert proposal.lock_version is not None

    def test_proposing_an_update_shows_what_it_would_replace(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        assert known_item.status is not None
        proposal = writer.propose_update(known_item.ref, {"status": "Closed"})
        before, after = proposal.changes["status"]
        assert before == known_item.status
        assert after == "Closed"

    def test_proposing_changes_nothing(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        # The whole point of the draft state. A proposal is not a write.
        writer.propose_update(known_item.ref, {"status": "Closed"})
        unchanged = writer.get_work_item(known_item.ref)
        assert unchanged is not None
        assert unchanged.status == known_item.status

    def test_proposing_a_create_names_the_project(
        self, writer: WorkManagementWriter, project: str
    ) -> None:
        proposal = writer.propose_create(project, {"title": "a new thing"})
        assert proposal.action is WriteAction.CREATE
        assert proposal.project == project

    def test_proposing_a_comment_carries_the_body(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        proposal = writer.propose_comment(known_item.ref, "why this closed")
        assert proposal.action is WriteAction.COMMENT
        assert proposal.body == "why this closed"

    def test_an_unknown_item_cannot_be_proposed_against(self, writer: WorkManagementWriter) -> None:
        from never4ga.errors import WorkItemNotFoundError

        with pytest.raises(WorkItemNotFoundError):
            writer.propose_update(
                ExternalId(provider=writer.provider_id, value="does-not-exist"),
                {"status": "Closed"},
            )

    # -- applying ---------------------------------------------------------

    def test_applying_an_update_changes_the_item(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        proposal = writer.propose_update(known_item.ref, {"status": "Closed"})
        result = writer.apply(proposal)
        assert result.item is not None
        assert result.item.status == "Closed"

    def test_applying_returns_the_item_as_the_provider_now_holds_it(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        # The response is the verification. Whatever the transport, a
        # writer hands back what the provider says is true now.
        result = writer.apply(writer.propose_update(known_item.ref, {"status": "Closed"}))
        read_again = writer.get_work_item(known_item.ref)
        assert read_again is not None
        assert result.item is not None
        assert result.item.status == read_again.status

    def test_applying_bumps_the_version_so_the_proposal_cannot_be_replayed(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        from never4ga.errors import WriteConflictError

        proposal = writer.propose_update(known_item.ref, {"status": "Closed"})
        writer.apply(proposal)
        with pytest.raises(WriteConflictError):
            writer.apply(proposal)

    def test_a_conflict_names_the_item(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        from never4ga.errors import WriteConflictError

        proposal = writer.propose_update(known_item.ref, {"status": "Closed"})
        writer.apply(proposal)
        with pytest.raises(WriteConflictError) as raised:
            writer.apply(proposal)
        assert known_item.ref.value in str(raised.value)

    def test_applying_a_create_produces_a_reachable_item(
        self, writer: WorkManagementWriter, project: str
    ) -> None:
        result = writer.apply(writer.propose_create(project, {"title": "a new thing"}))
        assert result.item is not None
        assert result.item.title == "a new thing"
        assert writer.get_work_item(result.item.ref) is not None

    def test_applying_a_comment_returns_something_that_identifies_it(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        # A second wrap edits the comment it made rather
        # than adding another, which it can only do if the first one came back
        # with an identity.
        result = writer.apply(writer.propose_comment(known_item.ref, "why this closed"))
        assert result.activity is not None
        assert result.item is None

    def test_amending_a_comment_replaces_it_rather_than_adding_one(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        first = writer.apply(writer.propose_comment(known_item.ref, "first account"))
        assert first.activity is not None
        second = writer.apply(
            writer.propose_comment(known_item.ref, "corrected account", amends=first.activity)
        )
        assert second.activity == first.activity

    def test_an_amendment_says_so_when_it_is_drafted(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        made = writer.apply(writer.propose_comment(known_item.ref, "first"))
        assert made.activity is not None
        proposal = writer.propose_comment(known_item.ref, "second", amends=made.activity)
        assert proposal.amends == made.activity
        assert "replace" in proposal.render()

    # -- projects ---------------------------------------------------------

    def test_a_project_that_is_not_there_is_absence_not_failure(
        self, writer: WorkManagementWriter
    ) -> None:
        assert writer.find_project("no-such-project-anywhere") is None

    def test_a_created_project_is_findable_by_its_identifier(
        self, writer: WorkManagementWriter
    ) -> None:
        made = writer.create_project("a-new-project", "A New Project")
        assert made.identifier == "a-new-project"
        found = writer.find_project("a-new-project")
        assert found is not None
        assert found.name == "A New Project"

    def test_creating_a_project_twice_is_refused_rather_than_silently_reused(
        self, writer: WorkManagementWriter
    ) -> None:
        from never4ga.errors import WriteRejectedError

        writer.create_project("a-new-project", "A New Project")
        with pytest.raises(WriteRejectedError):
            writer.create_project("a-new-project", "A New Project")

    def test_a_project_may_be_made_under_a_parent(self, writer: WorkManagementWriter) -> None:
        writer.create_project("the-parent", "The Parent")
        made = writer.create_project("the-child", "The Child", parent="the-parent")
        assert made.parent == "the-parent"


class RefusingWorkManagementWriterContract:
    """A writer whose provider does not permit what it was asked for.

    core/03 section 25: "An agent/UI must check capability availability rather
    than assume every tracker behaves like OpenProject." The refusal must be a
    raised type rather than a quietly absent effect.
    """

    @pytest.fixture
    def writer(self) -> WorkManagementWriter:
        raise NotImplementedError("supply a writer lacking every write capability")

    @pytest.fixture
    def known_item(self, writer: WorkManagementWriter) -> WorkItem:
        raise NotImplementedError("supply a work item the writer can reach")

    def test_declares_no_write_capability(self, writer: WorkManagementWriter) -> None:
        assert (
            not {
                WorkManagementCapability.CREATE_WORK_ITEM,
                WorkManagementCapability.UPDATE_WORK_ITEM,
                WorkManagementCapability.COMMENT_WORK_ITEM,
            }
            & writer.capabilities
        )

    def test_refuses_to_propose_an_update(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        with pytest.raises(CapabilityNotSupportedError):
            writer.propose_update(known_item.ref, {"status": "Closed"})

    def test_refuses_to_propose_a_create(self, writer: WorkManagementWriter) -> None:
        with pytest.raises(CapabilityNotSupportedError):
            writer.propose_create("any-project", {"title": "a new thing"})

    def test_refuses_to_propose_a_comment(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        with pytest.raises(CapabilityNotSupportedError):
            writer.propose_comment(known_item.ref, "why this closed")

    def test_refuses_to_apply_a_proposal_built_elsewhere(
        self, writer: WorkManagementWriter, known_item: WorkItem
    ) -> None:
        # A proposal is a value and can be carried between processes. The gate
        # has to be on applying too, not only on producing one.
        smuggled = ProposedMutation.update(
            known_item.ref, fields={"status": "Closed"}, lock_version=1
        )
        with pytest.raises(CapabilityNotSupportedError):
            writer.apply(smuggled)
