"""Turning a proposal into an OpenProject body, and checking what came back.

Pure functions over payloads, so every hazard of a live instance can be tested
against a captured payload.

Specification:
- details/openproject-adapter.md sections 6 and 7 -- writes use the current
  version, preserve provider validation, and use provider schema rather than
  hardcoding one instance's field configuration.

OpenProject's API v3 ignores an unknown field rather than rejecting it, the
form omits an unknown name from its echoed payload, and a PATCH response is
the whole resource. So a requested field absent from the response counts as a
divergence, not as unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest

from never4ga.adapters.openproject.write import (
    divergences,
    unknown_field_names,
    write_payload,
)
from never4ga.errors import WriteRejectedError

STATUS_HREF = "/api/v3/statuses/12"
PRIORITY_HREF = "/api/v3/priorities/8"
PARENT_HREF = "/api/v3/work_packages/544"


class TestPayload:
    def test_a_normalised_name_becomes_the_providers(self) -> None:
        # `title` is what every provider calls it; `subject` is OpenProject's.
        body = write_payload({"title": "a new subject"}, lock_version=3)
        assert body["subject"] == "a new subject"
        assert "title" not in body

    def test_the_version_travels_with_the_change(self) -> None:
        assert write_payload({"title": "x"}, lock_version=3)["lockVersion"] == 3

    def test_a_create_carries_no_version(self) -> None:
        assert "lockVersion" not in write_payload({"title": "x"}, lock_version=None)

    def test_a_description_is_a_formattable_object(self) -> None:
        body = write_payload({"description": "why this matters"}, lock_version=1)
        assert body["description"] == {"raw": "why this matters"}

    def test_dates_take_the_providers_spelling(self) -> None:
        body = write_payload({"start_date": "2026-08-26", "due_date": "2026-09-01"}, lock_version=1)
        assert body["startDate"] == "2026-08-26"
        assert body["dueDate"] == "2026-09-01"

    def test_a_status_is_a_link_rather_than_a_name(self) -> None:
        # Resolving a name to an href at the boundary: an id typo silently
        # targets the wrong status, a name typo cannot.
        body = write_payload({"status": "Closed"}, lock_version=1, links={"status": STATUS_HREF})
        assert body["_links"]["status"] == {"href": STATUS_HREF}
        assert "status" not in body

    def test_several_links_share_one_links_object(self) -> None:
        body = write_payload(
            {"status": "Closed", "priority": "High"},
            lock_version=1,
            links={"status": STATUS_HREF, "priority": PRIORITY_HREF},
        )
        assert set(body["_links"]) == {"status", "priority"}

    def test_a_linked_field_without_a_resolved_link_is_refused(self) -> None:
        # Better than sending a name the API would ignore in silence.
        with pytest.raises(WriteRejectedError, match="status"):
            write_payload({"status": "Nonexistent"}, lock_version=1, links={})

    def test_a_parent_travels_as_a_link_rather_than_an_id(self) -> None:
        # Hierarchy is a link on the work package: `parent` in the body is
        # ignored.
        body = write_payload({"parent": "544"}, lock_version=1, links={"parent": PARENT_HREF})
        assert body["_links"]["parent"] == {"href": PARENT_HREF}
        assert "parent" not in body

    def test_an_empty_parent_detaches_instead_of_resolving(self) -> None:
        # `{"href": None}` is how OpenProject spells "no parent". No id to
        # verify, so resolution is skipped rather than failing the write.
        body = write_payload({"parent": ""}, lock_version=1, links={})
        assert body["_links"]["parent"] == {"href": None}

    def test_clearing_a_link_is_satisfied_by_its_absence(self) -> None:
        # A detach asks for {"href": None}. The provider answers by not
        # carrying the link at all, which is success. Reading it as "not
        # mentioned" would report every successful detach as a failed write.
        sent = {"_links": {"parent": {"href": None}}}
        answered: dict[str, Any] = {"_links": {}}
        assert divergences(sent, answered) == ()

    def test_a_link_that_would_not_clear_is_still_reported(self) -> None:
        sent = {"_links": {"parent": {"href": None}}}
        answered = {"_links": {"parent": {"href": PARENT_HREF, "title": "still there"}}}
        assert divergences(sent, answered)

    def test_a_parent_that_did_not_resolve_is_refused(self) -> None:
        # An id nobody has must not be posted. The API answers 422 about it
        # only sometimes, so the refusal has to happen here.
        with pytest.raises(WriteRejectedError, match="parent"):
            write_payload({"parent": "999999"}, lock_version=1, links={})

    def test_a_field_this_adapter_cannot_write_is_refused_by_name(self) -> None:
        # `assignee` needs a principal id, and resolving a person's name to one
        # is not something this adapter has verified. Refusing beats sending a
        # field the API would ignore without saying so.
        with pytest.raises(WriteRejectedError) as raised:
            write_payload({"assignee": "ada"}, lock_version=1)
        assert "assignee" in str(raised.value)

    def test_a_comment_never_rides_inside_a_write_body(self) -> None:
        # A `comment` key in a PATCH returns 200 and is dropped in silence.
        # It is its own request, and this refuses rather than lets a
        # caller believe otherwise.
        with pytest.raises(WriteRejectedError, match="comment"):
            write_payload({"comment": "why this closed"}, lock_version=1)


class TestUnknownFieldNames:
    def test_a_name_the_schema_does_not_have_is_reported(self) -> None:
        # The form answers 200 with no validation error for an unknown name, so
        # the schema is the oracle rather than the form's verdict.
        schema = {"subject": {"writable": True}, "lockVersion": {"writable": True}}
        assert unknown_field_names({"subject": "x", "madeUp": 1}, schema) == ("madeUp",)

    def test_links_are_checked_by_their_own_names(self) -> None:
        schema = {"subject": {"writable": True}, "status": {"writable": True}}
        body = {"subject": "x", "_links": {"status": {"href": STATUS_HREF}}}
        assert unknown_field_names(body, schema) == ()

    def test_an_unknown_link_is_reported(self) -> None:
        schema = {"subject": {"writable": True}}
        body = {"subject": "x", "_links": {"invented": {"href": "/x"}}}
        assert unknown_field_names(body, schema) == ("invented",)

    def test_the_version_is_not_a_field_to_look_for(self) -> None:
        assert unknown_field_names({"lockVersion": 3}, {"subject": {}}) == ()

    def test_an_empty_schema_reports_nothing_rather_than_everything(self) -> None:
        # A form that could not be read is not evidence that every name is
        # wrong. Verification against the response still stands behind this.
        assert unknown_field_names({"subject": "x"}, {}) == ()


class TestDivergences:
    def test_a_field_that_came_back_as_asked_is_not_a_divergence(self) -> None:
        assert divergences({"subject": "new"}, {"subject": "new", "lockVersion": 4}) == ()

    def test_a_field_that_came_back_different_is_reported(self) -> None:
        found = divergences({"subject": "new"}, {"subject": "old"})
        assert len(found) == 1
        assert "subject" in found[0]

    def test_a_field_absent_from_the_response_is_a_divergence(self) -> None:
        # The rule that keeps this correct under any future API: the response
        # is whole today, and a version that answered with less must fail
        # loudly rather than pass silently.
        found = divergences({"subject": "new"}, {"lockVersion": 4})
        assert len(found) == 1
        assert "subject" in found[0]

    def test_the_version_is_expected_to_move_and_is_not_compared(self) -> None:
        assert (
            divergences({"subject": "x", "lockVersion": 3}, {"subject": "x", "lockVersion": 4})
            == ()
        )

    def test_a_description_is_compared_on_its_raw_text(self) -> None:
        sent = {"description": {"raw": "why"}}
        answered = {"description": {"format": "markdown", "raw": "why", "html": "<p>why</p>"}}
        assert divergences(sent, answered) == ()

    def test_a_description_that_did_not_take_is_reported(self) -> None:
        sent = {"description": {"raw": "why"}}
        answered = {"description": {"format": "markdown", "raw": "", "html": ""}}
        assert divergences(sent, answered) != ()

    def test_a_link_is_compared_on_its_href(self) -> None:
        sent = {"_links": {"status": {"href": STATUS_HREF}}}
        answered = {"_links": {"status": {"href": STATUS_HREF, "title": "Closed"}}}
        assert divergences(sent, answered) == ()

    def test_a_link_that_did_not_take_is_reported(self) -> None:
        sent = {"_links": {"status": {"href": STATUS_HREF}}}
        answered = {"_links": {"status": {"href": "/api/v3/statuses/1", "title": "New"}}}
        found = divergences(sent, answered)
        assert len(found) == 1
        assert "status" in found[0]

    def test_an_absent_link_is_a_divergence(self) -> None:
        sent = {"_links": {"status": {"href": STATUS_HREF}}}
        assert divergences(sent, {"_links": {}}) != ()

    def test_the_unknown_field_hazard_is_caught(self) -> None:
        # The whole reason this function exists. A PATCH carrying a name the
        # instance does not have returns 200, changes nothing, and bumps no
        # version -- indistinguishable from success without this.
        sent = {"thisFieldDoesNotExist": "x"}
        answered = {"subject": "unchanged", "lockVersion": 1}
        assert divergences(sent, answered) != ()
