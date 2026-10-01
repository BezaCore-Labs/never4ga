"""The OpenProject writer: propose, validate, apply, verify, conflict.

Specification:
- core/03 section 22 -- draft/proposed writes are distinct from actual ones,
  and stale state must never overwrite newer.
- details/openproject-adapter.md sections 6 and 7 -- read current state, use
  the current version, prefer provider schema over a hardcoded field list, and
  return conflict errors rather than overwriting.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from never4ga.adapters.openproject import OpenProjectProvider, OpenProjectWriter
from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ExternalId
from never4ga.errors import (
    CapabilityNotSupportedError,
    ProviderUnavailableError,
    WorkItemNotFoundError,
    WriteConflictError,
    WriteRejectedError,
)
from never4ga.ports.work_management import ProposedMutation, WorkManagementWriter, WriteAction
from tests.openproject_fixtures import (
    BASE_URL,
    KNOWN_OTHER_WORK_PACKAGE,
    KNOWN_WORK_PACKAGE,
    PROJECT_REF,
    fixture_transport,
    offline_transport,
    writable_transport,
)

REF = ExternalId(provider="openproject", value=KNOWN_WORK_PACKAGE)
OTHER = ExternalId(provider="openproject", value=KNOWN_OTHER_WORK_PACKAGE)


def _transport_with_no_work_packages() -> httpx.MockTransport:
    """The captured instance, with an empty work package collection.

    A project nobody has filed anything in yet. The collection still answers,
    and still carries its own action links -- what it has no longer got is an
    element whose links say whether this token may update or comment.
    """
    inner = writable_transport()

    def handle(request: httpx.Request) -> httpx.Response:
        response = inner.handle_request(request)
        if response.status_code != 200:
            return response
        payload = json.loads(response.content)
        if not isinstance(payload, dict) or payload.get("_type") != "Collection":
            return response
        if "work_packages" not in urlparse(str(request.url)).path:
            return response
        payload["_embedded"]["elements"] = []
        payload["total"] = 0
        payload["count"] = 0
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handle)


def writer(transport: httpx.MockTransport | None = None) -> OpenProjectWriter:
    return OpenProjectWriter(
        base_url=BASE_URL,
        token="not-a-real-token",
        connection="work_openproject",
        project_ref=PROJECT_REF,
        transport=transport or writable_transport(),
    )


class TestRole:
    def test_a_writer_satisfies_the_writer_protocol(self) -> None:
        assert isinstance(writer(), WorkManagementWriter)

    def test_the_read_only_provider_does_not(self) -> None:
        # The two-Protocol split, demonstrated on the real adapter rather than
        # only on a fake: a composition root that reads gets something that
        # structurally cannot write.
        reader = OpenProjectProvider(
            base_url=BASE_URL,
            token="not-a-real-token",
            connection="work_openproject",
            transport=writable_transport(),
        )
        assert not isinstance(reader, WorkManagementWriter)

    def test_a_writer_still_reads(self) -> None:
        found = writer().get_work_item(REF)
        assert found is not None
        assert found.title


class TestCapabilities:
    def test_a_writer_claims_what_the_token_may_do_and_it_can_do(self) -> None:
        assert WorkManagementCapability.UPDATE_WORK_ITEM in writer().capabilities

    def test_a_reader_claims_no_write_however_permissive_the_token(self) -> None:
        # Discovery reads permissions off HAL action links, and the captured
        # instance grants all of them. What a class can do is a different
        # question, and `capabilities` answers the intersection: a capability
        # with no method behind it is a claim.
        reader = OpenProjectProvider(
            base_url=BASE_URL,
            token="not-a-real-token",
            connection="work_openproject",
            project_ref=PROJECT_REF,
            transport=fixture_transport(),
        )
        assert (
            not {
                WorkManagementCapability.CREATE_WORK_ITEM,
                WorkManagementCapability.UPDATE_WORK_ITEM,
                WorkManagementCapability.COMMENT_WORK_ITEM,
            }
            & reader.capabilities
        )
        assert WorkManagementCapability.READ_WORK_ITEMS in reader.capabilities

    def test_every_write_the_token_permits_is_now_claimed(self) -> None:
        # The class implements every write, so the token alone narrows the
        # set. A token without the permission still loses the capability,
        # which the discovery tests below cover.
        found = writer().capabilities
        assert {
            WorkManagementCapability.CREATE_WORK_ITEM,
            WorkManagementCapability.UPDATE_WORK_ITEM,
            WorkManagementCapability.COMMENT_WORK_ITEM,
        } <= found

    def test_a_write_is_still_refused_when_the_token_may_not_make_it(self) -> None:
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            response = inner.handle_request(request)
            if request.method == "GET" and response.status_code == 200:
                payload = json.loads(response.content)
                for element in payload.get("_embedded", {}).get("elements", []):
                    element.get("_links", {}).pop("addComment", None)
                return httpx.Response(200, json=payload)
            return response

        built = writer(httpx.MockTransport(handle))
        assert WorkManagementCapability.COMMENT_WORK_ITEM not in built.capabilities
        with pytest.raises(CapabilityNotSupportedError):
            built.propose_comment(REF, "why this closed")

    def test_a_token_that_may_not_update_gets_no_update_capability(self) -> None:
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            response = inner.handle_request(request)
            if request.method == "GET" and response.status_code == 200:
                import json

                payload = json.loads(response.content)
                for element in payload.get("_embedded", {}).get("elements", []):
                    element.get("_links", {}).pop("update", None)
                    element.get("_links", {}).pop("updateImmediately", None)
                return httpx.Response(200, json=payload)
            return response

        assert (
            WorkManagementCapability.UPDATE_WORK_ITEM
            not in writer(httpx.MockTransport(handle)).capabilities
        )


class _FullyCapableWriter(OpenProjectWriter):
    """A writer that claims every write, to test discovery on its own.

    `capabilities` reports the intersection of what the instance permits and
    what the class implements. This subclass takes the class's half out of the
    intersection, so what the tests see is discovery alone.
    """

    _WRITABLE = frozenset(
        {
            WorkManagementCapability.CREATE_WORK_ITEM,
            WorkManagementCapability.UPDATE_WORK_ITEM,
            WorkManagementCapability.COMMENT_WORK_ITEM,
        }
    )


def _without_links(*names: str, on_collection: bool = False) -> httpx.MockTransport:
    """The captured instance, minus action links a token would not be given."""
    inner = fixture_transport()

    def handle(request: httpx.Request) -> httpx.Response:
        response = inner.handle_request(request)
        if response.status_code != 200:
            return response
        payload = json.loads(response.content)
        if not isinstance(payload, dict) or "_embedded" not in payload:
            return response
        targets = [payload] if on_collection else payload["_embedded"].get("elements", [])
        for target in targets:
            for name in names:
                target.get("_links", {}).pop(name, None)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handle)


def _capable(transport: httpx.MockTransport) -> frozenset[WorkManagementCapability]:
    return _FullyCapableWriter(
        base_url=BASE_URL,
        token="not-a-real-token",
        connection="work_openproject",
        project_ref=PROJECT_REF,
        transport=transport,
    ).capabilities


class TestPermissionDiscovery:
    def test_writes_come_from_what_the_token_may_do(self) -> None:
        # details/openproject-adapter.md section 12: capability depends on the
        # authenticated permissions. OpenProject omits an action link the user
        # cannot perform, so the links ARE the oracle.
        found = _capable(fixture_transport())
        assert WorkManagementCapability.UPDATE_WORK_ITEM in found
        assert WorkManagementCapability.COMMENT_WORK_ITEM in found
        assert WorkManagementCapability.CREATE_WORK_ITEM in found

    def test_an_item_the_token_may_not_change_is_not_claimed_updatable(self) -> None:
        found = _capable(_without_links("update", "updateImmediately"))
        assert WorkManagementCapability.UPDATE_WORK_ITEM not in found
        # Losing one permission must not cost the others.
        assert WorkManagementCapability.COMMENT_WORK_ITEM in found
        assert WorkManagementCapability.READ_WORK_ITEMS in found

    def test_an_item_the_token_may_not_comment_on_is_not_claimed(self) -> None:
        found = _capable(_without_links("addComment"))
        assert WorkManagementCapability.COMMENT_WORK_ITEM not in found
        assert WorkManagementCapability.UPDATE_WORK_ITEM in found

    def test_creation_is_read_from_the_collection_not_the_item(self) -> None:
        # `createWorkPackage` is a property of the collection: an item cannot
        # say whether another one may be made. The spelling difference is the
        # API's -- a collection offers `createWorkPackageImmediate` where a
        # project offers `createWorkPackageImmediately`.
        found = _capable(
            _without_links("createWorkPackage", "createWorkPackageImmediate", on_collection=True)
        )
        assert WorkManagementCapability.CREATE_WORK_ITEM not in found
        assert WorkManagementCapability.UPDATE_WORK_ITEM in found


class TestProposing:
    def test_a_proposal_carries_the_current_version(self) -> None:
        proposal = writer().propose_update(REF, {"status": "Closed"})
        assert proposal.action is WriteAction.UPDATE
        assert proposal.lock_version == 1

    def test_a_proposal_shows_what_it_would_replace(self) -> None:
        proposal = writer().propose_update(REF, {"status": "Closed"})
        assert proposal.changes["status"] == ("New", "Closed")

    def test_proposing_changes_nothing(self) -> None:
        built = writer()
        built.propose_update(REF, {"status": "Closed"})
        after = built.get_work_item(REF)
        assert after is not None
        assert after.status == "New"

    def test_an_unknown_item_is_absence_not_failure_of_the_tracker(self) -> None:
        with pytest.raises(WorkItemNotFoundError):
            writer().propose_update(
                ExternalId(provider="openproject", value="404404"), {"status": "Closed"}
            )

    def test_a_field_name_the_instance_does_not_have_is_refused_early(self) -> None:
        # Caught before anything is sent. The form answers 200 for an unknown
        # name, so this is a client-side check against the schema rather than
        # the form's verdict.
        with pytest.raises(WriteRejectedError, match="madeUpField"):
            writer().propose_update(REF, {"madeUpField": "x"})

    def test_a_value_the_instance_refuses_is_reported_before_sending(self) -> None:
        with pytest.raises(WriteRejectedError, match="blank"):
            writer().propose_update(REF, {"title": ""})

    def test_a_status_name_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(WriteRejectedError, match="status"):
            writer().propose_update(REF, {"status": "Nonexistent"})

    def test_a_status_name_is_matched_without_regard_to_case(self) -> None:
        assert writer().propose_update(REF, {"status": "closed"}).lock_version == 1

    def test_a_parent_is_resolved_by_reading_the_item(self) -> None:
        # The other linked fields translate a name into an id. A parent is
        # already an id, so resolution earns its place by proving the item
        # exists rather than by translating anything.
        assert writer().propose_update(REF, {"parent": KNOWN_WORK_PACKAGE}).lock_version == 1

    def test_a_parent_id_nobody_has_is_refused(self) -> None:
        # Posting it instead gets a 422 about the parent, and only sometimes.
        with pytest.raises(WriteRejectedError, match="parent"):
            writer().propose_update(REF, {"parent": "999999"})

    def test_an_empty_parent_detaches_rather_than_being_refused(self) -> None:
        # Attach-only would make hierarchy write-once: settable and never
        # undoable. An empty value is the only thing `--field parent=` can
        # spell, and nothing else it could mean is ambiguous with detaching.
        assert writer().propose_update(REF, {"parent": ""}).lock_version == 1

    def test_an_unreadable_form_does_not_block_a_write(self) -> None:
        # The form makes a failure legible early; the response comparison is
        # the check that cannot be skipped. Losing the first must not cost the
        # ability to write at all.
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/form"):
                return httpx.Response(500, json={"message": "no"})
            return inner.handle_request(request)

        proposal = writer(httpx.MockTransport(handle)).propose_update(REF, {"title": "fine"})
        assert proposal.changes["title"][1] == "fine"


class TestCreating:
    def test_a_proposal_names_the_project_and_creates_nothing(self) -> None:
        built = writer()
        proposal = built.propose_create(PROJECT_REF, {"title": "a new thing"})
        assert proposal.action is WriteAction.CREATE
        assert proposal.project == PROJECT_REF
        assert proposal.lock_version is None

    def test_the_connections_project_stands_in_when_none_is_named(self) -> None:
        assert writer().propose_create("", {"title": "x"}).project == PROJECT_REF

    def test_a_value_the_instance_refuses_is_caught_before_creating(self) -> None:
        with pytest.raises(WriteRejectedError, match="blank"):
            writer().propose_create(PROJECT_REF, {"title": ""})

    def test_a_field_name_the_instance_does_not_have_is_refused(self) -> None:
        with pytest.raises(WriteRejectedError, match="madeUpField"):
            writer().propose_create(PROJECT_REF, {"title": "x", "madeUpField": 1})

    def test_applying_produces_a_reachable_item(self) -> None:
        built = writer()
        result = built.apply(built.propose_create(PROJECT_REF, {"title": "a new thing"}))
        assert result.item is not None
        assert result.item.title == "a new thing"
        assert built.get_work_item(result.item.ref) is not None

    def test_a_created_item_carries_a_url_a_person_can_open(self) -> None:
        built = writer()
        result = built.apply(built.propose_create(PROJECT_REF, {"title": "a new thing"}))
        assert result.item is not None
        assert result.item.url is not None
        assert result.item.ref.value in result.item.url

    def test_the_project_is_addressed_by_slug_in_the_path(self) -> None:
        # The one place the API's relaxation about project references does not
        # reach: a numeric id works wherever a project href appears in a body,
        # and not in this path segment.
        seen: list[str] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                seen.append(request.url.path)
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        built.apply(built.propose_create(PROJECT_REF, {"title": "a new thing"}))
        assert seen[-1] == f"/api/v3/projects/{PROJECT_REF}/work_packages"

    def test_creating_without_a_project_anywhere_is_refused(self) -> None:
        built = OpenProjectWriter(
            base_url=BASE_URL,
            token="not-a-real-token",
            connection="work_openproject",
            transport=writable_transport(),
        )
        with pytest.raises(WriteRejectedError, match="without a project"):
            built.propose_create("", {"title": "x"})

    def test_a_creation_that_did_not_take_fails_loudly(self) -> None:
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            response = inner.handle_request(request)
            if request.method == "POST" and response.status_code == 201:
                payload = json.loads(response.content)
                payload["subject"] = "something else entirely"
                return httpx.Response(201, json=payload)
            return response

        built = writer(httpx.MockTransport(handle))
        with pytest.raises(WriteRejectedError, match="a new work item"):
            built.apply(built.propose_create(PROJECT_REF, {"title": "asked for"}))


class TestCommenting:
    def test_a_comment_is_its_own_request(self) -> None:
        seen: list[tuple[str, str]] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        built.apply(built.propose_comment(REF, "why this closed"))
        assert ("POST", f"/api/v3/work_packages/{KNOWN_WORK_PACKAGE}/activities") in seen
        assert not [one for one in seen if one[0] == "PATCH"]

    def test_a_comment_comes_back_with_an_identity(self) -> None:
        built = writer()
        result = built.apply(built.propose_comment(REF, "why this closed"))
        assert result.activity is not None
        assert result.item is None

    def test_an_unknown_item_cannot_be_commented_on(self) -> None:
        with pytest.raises(WorkItemNotFoundError):
            writer().propose_comment(ExternalId(provider="openproject", value="404404"), "x")

    def test_posting_sends_the_formattable_object(self) -> None:
        sent: list[object] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/activities"):
                sent.append(json.loads(request.content))
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        built.apply(built.propose_comment(REF, "why this closed"))
        assert sent == [{"comment": {"raw": "why this closed"}}]

    def test_amending_sends_a_bare_string_and_not_the_object(self) -> None:
        """The asymmetry, pinned so nobody tidies it into consistency.

        `POST .../activities` wants `{"comment": {"raw": ...}}` and
        `PATCH /activities/{id}` wants a bare string, refusing the object with
        `400 Bad request: comment is invalid`, a message that reads like a
        permission problem and is not. A GET on the activity hands back the
        object form, so echoing what was just read is exactly what fails.
        """
        sent: list[object] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH" and "/activities/" in request.url.path:
                sent.append(json.loads(request.content))
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        made = built.apply(built.propose_comment(REF, "first account"))
        assert made.activity is not None
        built.apply(built.propose_comment(REF, "corrected account", amends=made.activity))
        assert sent == [{"comment": "corrected account"}]

    def test_amending_replaces_rather_than_adds(self) -> None:
        built = writer()
        made = built.apply(built.propose_comment(REF, "first account"))
        assert made.activity is not None
        again = built.apply(built.propose_comment(REF, "corrected account", amends=made.activity))
        assert again.activity == made.activity

    def test_a_comment_that_does_not_say_what_was_asked_fails_loudly(self) -> None:
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            response = inner.handle_request(request)
            if request.url.path.endswith("/activities") and response.status_code == 201:
                payload = json.loads(response.content)
                payload["comment"]["raw"] = "something else"
                return httpx.Response(201, json=payload)
            return response

        built = writer(httpx.MockTransport(handle))
        with pytest.raises(WriteRejectedError, match="does not say what was asked"):
            built.apply(built.propose_comment(REF, "why this closed"))


class TestApplying:
    def test_applying_makes_the_change(self) -> None:
        built = writer()
        result = built.apply(built.propose_update(REF, {"status": "Closed"}))
        assert result.item is not None
        assert result.item.status == "Closed"

    def test_the_returned_item_is_what_the_provider_now_holds(self) -> None:
        built = writer()
        result = built.apply(built.propose_update(REF, {"title": "a new subject"}))
        read_again = built.get_work_item(REF)
        assert result.item is not None
        assert read_again is not None
        assert result.item.title == read_again.title == "a new subject"

    def test_a_stale_proposal_is_refused_rather_than_applied(self) -> None:
        built = writer()
        proposal = built.propose_update(REF, {"status": "Closed"})
        built.apply(proposal)
        with pytest.raises(WriteConflictError):
            built.apply(proposal)

    def test_nothing_retries_after_a_conflict(self) -> None:
        # A retry with a refetched version is the silent overwrite core/03
        # section 22 forbids. The item must be exactly as the first write left
        # it, not as the replayed one asked for.
        built = writer()
        first = built.propose_update(REF, {"title": "first"})
        built.apply(first)
        second = built.propose_update(REF, {"title": "second"})
        built.apply(second)
        with pytest.raises(WriteConflictError):
            built.apply(second)
        after = built.get_work_item(REF)
        assert after is not None
        assert after.title == "second"


class TestVerification:
    def test_a_200_that_did_not_change_anything_fails_loudly(self) -> None:
        # OpenProject ignores an unknown field, answers 200 and bumps nothing.
        # Without this check the caller is told a typo worked.
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH":
                import json

                body = json.loads(request.content)
                body.pop("subject", None)
                request = httpx.Request("PATCH", request.url, json=body, headers=request.headers)
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        proposal = built.propose_update(REF, {"title": "will be dropped"})
        with pytest.raises(WriteRejectedError, match="without making the change"):
            built.apply(proposal)

    def test_a_field_absent_from_the_answer_counts_as_divergence(self) -> None:
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            response = inner.handle_request(request)
            if request.method == "PATCH" and response.status_code == 200:
                import json

                payload = json.loads(response.content)
                payload.pop("subject", None)
                return httpx.Response(200, json=payload)
            return response

        built = writer(httpx.MockTransport(handle))
        with pytest.raises(WriteRejectedError):
            built.apply(built.propose_update(REF, {"title": "asked for"}))

    def test_a_verified_write_says_nothing_alarming(self) -> None:
        built = writer()
        result = built.apply(built.propose_update(REF, {"title": "a new subject"}))
        assert result.proposal.action is WriteAction.UPDATE
        assert result.activity is None


class TestRequestShape:
    def test_a_write_never_follows_a_redirect(self) -> None:
        # A read follows a same-origin redirect because OpenProject rewrites a
        # nested collection into a filtered one. Replaying a body that changes
        # something against a path the server picked is a different matter.
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH":
                return httpx.Response(308, headers={"Location": "/api/v3/work_packages/1"})
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        proposal = built.propose_update(REF, {"title": "x"})
        with pytest.raises(Exception, match="does not follow"):
            built.apply(proposal)

    def test_a_status_travels_as_a_link_and_never_as_a_name(self) -> None:
        seen: list[dict[str, object]] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH":
                import json

                seen.append(json.loads(request.content))
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        built.apply(built.propose_update(REF, {"status": "Closed"}))
        assert seen[0]["_links"] == {"status": {"href": "/api/v3/statuses/12"}}
        assert "status" not in seen[0]


class TestCapabilityOnAnEmptyProject:
    """A project nobody has filed anything in yet.

    The write half is read off an existing work package's action links, so an
    empty collection cannot measure it. Unmeasured is not refused, and it must
    not be remembered: a cached answer would report create-only for the life of
    the process, long after the project's first item arrived.
    """

    def test_the_read_half_is_still_reported(self) -> None:
        found = writer(_transport_with_no_work_packages()).capabilities
        assert WorkManagementCapability.READ_WORK_ITEMS in found

    def test_the_write_half_is_not_claimed_without_evidence(self) -> None:
        found = writer(_transport_with_no_work_packages()).capabilities
        assert WorkManagementCapability.UPDATE_WORK_ITEM not in found
        assert WorkManagementCapability.COMMENT_WORK_ITEM not in found

    def test_it_is_asked_again_once_the_project_holds_something(self) -> None:
        inner = writable_transport()
        empty = _transport_with_no_work_packages()
        filled = False

        def handle(request: httpx.Request) -> httpx.Response:
            return (inner if filled else empty).handle_request(request)

        built = writer(httpx.MockTransport(handle))
        assert WorkManagementCapability.UPDATE_WORK_ITEM not in built.capabilities
        filled = True
        assert WorkManagementCapability.UPDATE_WORK_ITEM in built.capabilities


class TestAnAssigneeResolvesLikeAnyOtherLink:
    """`assignee` and `responsible` resolve by name, like any other link.

    `GET /api/v3/principals` answers with a name and a self href, exactly as
    `statuses`, `priorities` and `types` do, so the same resolution works
    unchanged. Resolving by name assumes nothing about who uses the instance,
    so an instance with a team works as well as one with a single user.
    """

    def _sent(self, fields: dict[str, object]) -> dict[str, object]:
        import json

        seen: list[dict[str, object]] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH":
                seen.append(json.loads(request.content))
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        built.apply(built.propose_update(REF, fields))
        return seen[0]

    def test_an_assignee_travels_as_a_link(self) -> None:
        body = self._sent({"assignee": "Sam Okafor"})
        assert body["_links"] == {"assignee": {"href": "/api/v3/users/103"}}
        assert "assignee" not in body

    def test_a_responsible_party_resolves_the_same_way(self) -> None:
        body = self._sent({"responsible": "Instance Admin"})
        assert body["_links"] == {"responsible": {"href": "/api/v3/users/101"}}

    def test_the_name_is_matched_without_regard_to_case(self) -> None:
        """As every other linked field is."""
        assert self._sent({"assignee": "INSTANCE ADMIN"})["_links"] == {
            "assignee": {"href": "/api/v3/users/101"}
        }

    def test_a_name_nobody_has_is_refused_rather_than_sent(self) -> None:
        """The guarantee the other linked fields already make.

        An unresolvable name must never reach the instance: a name that does
        not exist fails loudly, where an id that does not exist silently
        targets somebody else.
        """
        built = writer(writable_transport())
        with pytest.raises(Exception, match="assignee"):
            built.apply(built.propose_update(REF, {"assignee": "Nobody At All"}))

    def test_a_name_two_people_share_is_refused(self) -> None:
        """Instance-wide, unlike a status, so names are not unique by design.

        The fixture carries "Alex Rivera" and "alex rivera" because a real
        instance can. Picking one would assign work to whichever the collection
        happened to list last, which is the silent-wrong-target failure the
        resolve-by-name rule exists to prevent.
        """
        built = writer(writable_transport())
        with pytest.raises(Exception, match="more than one"):
            built.apply(built.propose_update(REF, {"assignee": "Alex Rivera"}))


class TestARelationIsAWriteLikeAnyOther:
    """A relation is written through the same propose-and-apply path.

    A relation is its own resource with its own collection, which is why it is
    a fourth `WriteAction` rather than a field. It has no `lockVersion` to be
    computed against and cannot travel inside an update, for the same reason a
    comment is its own request.
    """

    def test_both_ends_are_read_before_anything_is_drafted(self) -> None:
        """A typo must fail here rather than becoming a relation to nothing.

        The API answers 422 for an unknown far end and says little about which
        end was wrong, so the adapter applies the rule every other linked value
        follows: what cannot be resolved is refused, never sent.
        """
        built = writer(writable_transport())
        with pytest.raises(WorkItemNotFoundError):
            built.propose_relation(
                REF, ExternalId(provider="openproject", value="99999"), kind="relates"
            )

    def test_the_relation_travels_as_a_link_and_a_type(self) -> None:
        import json

        seen: list[dict[str, object]] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/relations"):
                seen.append(json.loads(request.content))
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        built.apply(built.propose_relation(REF, OTHER, kind="blocks"))

        assert seen[0]["type"] == "blocks"
        assert seen[0]["_links"] == {"to": {"href": f"/api/v3/work_packages/{OTHER.value}"}}

    def test_a_description_rides_along_when_there_is_one(self) -> None:
        import json

        seen: list[dict[str, object]] = []
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/relations"):
                seen.append(json.loads(request.content))
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        built.apply(built.propose_relation(REF, OTHER, kind="relates", description="same gap"))
        assert seen[0]["description"] == "same gap"

    def test_a_kind_the_instance_changed_is_refused(self) -> None:
        """The read-back rule, applied to the one field a relation has.

        OpenProject's PATCH ignores unknown top-level fields rather than
        rejecting them, so every write path here compares what came back
        against what was asked. A relation is no different.
        """
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/relations"):
                return httpx.Response(
                    201, json={"_type": "Relation", "id": 99, "type": "relates", "_links": {}}
                )
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        proposal = built.propose_relation(REF, OTHER, kind="blocks")
        with pytest.raises(Exception, match="where 'blocks' was asked for"):
            built.apply(proposal)

    def test_precedes_is_accepted_when_the_instance_stores_it_as_follows(self) -> None:
        """The same link named from its other end is a success.

        Asked for "A precedes B", the instance stores and answers `follows`
        from B to A. The write succeeded and must be reported as one.
        """
        built = writer(writable_transport())
        result = built.apply(built.propose_relation(REF, OTHER, kind="precedes"))
        assert result.item is not None

    @staticmethod
    def _answering(relation: dict[str, object]) -> httpx.MockTransport:
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/relations"):
                return httpx.Response(201, json={"_type": "Relation", "id": 99, **relation})
            return inner.handle_request(request)

        return httpx.MockTransport(handle)

    @staticmethod
    def _ends(source: str, target: str) -> dict[str, object]:
        return {
            "from": {"href": f"/api/v3/work_packages/{source}"},
            "to": {"href": f"/api/v3/work_packages/{target}"},
        }

    def test_the_reverse_pair_is_accepted_whichever_way_round(self) -> None:
        built = writer(
            self._answering(
                {
                    "type": "precedes",
                    "reverseType": "follows",
                    "_links": self._ends(OTHER.value, REF.value),
                }
            )
        )
        result = built.apply(built.propose_relation(REF, OTHER, kind="follows"))
        assert result.item is not None

    def test_the_asked_type_pointing_the_wrong_way_is_refused(self) -> None:
        # `blocks` from OTHER to REF is the opposite of what was asked, and a
        # comparison of the type alone would have accepted it.
        built = writer(
            self._answering(
                {
                    "type": "blocks",
                    "reverseType": "blocked",
                    "_links": self._ends(OTHER.value, REF.value),
                }
            )
        )
        with pytest.raises(WriteRejectedError, match="where 'blocks' was asked for"):
            built.apply(built.propose_relation(REF, OTHER, kind="blocks"))

    def test_a_relation_to_some_other_item_is_refused(self) -> None:
        built = writer(
            self._answering(
                {
                    "type": "blocks",
                    "reverseType": "blocked",
                    "_links": self._ends(REF.value, "12345"),
                }
            )
        )
        with pytest.raises(WriteRejectedError, match="where 'blocks' was asked for"):
            built.apply(built.propose_relation(REF, OTHER, kind="blocks"))

    def test_it_hands_back_the_near_end_rather_than_the_relation(self) -> None:
        """`WriteResult.item` means the item as the provider now holds it.

        The POST answers with the relation, which is not that -- so the near
        end is read again. That read is also what proves the link landed: a 201
        says a request succeeded, and the work package is what says the thing
        asked for happened.
        """
        built = writer(writable_transport())
        result = built.apply(built.propose_relation(REF, OTHER, kind="relates"))
        assert result.item is not None
        assert result.item.ref == REF


class TestAnUnreachableInstanceIsNotAnIncapableOne:
    """An outage is reported as an outage, never as a missing capability.

    When discovery reaches nothing it returns an empty capability set, which is
    right for a read. A write must not check a capability against that set and
    report the operation as unsupported.

    The two errors ask for opposite things. Unreachable means wait and retry;
    unsupported means stop, because no retry will help. An agent told the
    second gives up on work it should have kept.
    """

    @pytest.mark.parametrize(
        "propose",
        [
            lambda built: built.propose_update(REF, {"status": "Closed"}),
            lambda built: built.propose_create(PROJECT_REF, {"subject": "A new item"}),
            lambda built: built.propose_comment(REF, "why this closed"),
            lambda built: built.propose_relation(REF, OTHER, kind="relates"),
        ],
        ids=["update", "create", "comment", "relate"],
    )
    def test_every_write_says_the_instance_did_not_answer(self, propose: Any) -> None:
        built = writer(offline_transport())
        with pytest.raises(ProviderUnavailableError, match="did not answer"):
            propose(built)

    def test_applying_a_proposal_made_elsewhere_says_so_too(self) -> None:
        # A proposal is a value and can travel, so `apply` has its own gate.
        built = writer(offline_transport())
        with pytest.raises(ProviderUnavailableError, match="did not answer"):
            built.apply(ProposedMutation.comment(REF, "why this closed"))

    def test_a_refusal_is_still_reported_as_one(self) -> None:
        # The instance answered, and the answer was no. That is not an outage
        # and must not start reading as one.
        inner = writable_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            response = inner.handle_request(request)
            if request.method == "GET" and response.status_code == 200:
                payload = json.loads(response.content)
                for element in payload.get("_embedded", {}).get("elements", []):
                    element.get("_links", {}).pop("addComment", None)
                return httpx.Response(200, json=payload)
            return response

        with pytest.raises(CapabilityNotSupportedError):
            writer(httpx.MockTransport(handle)).propose_comment(REF, "why this closed")

    def test_a_partial_outage_is_not_remembered_as_a_settled_answer(self) -> None:
        # The write half is read off the work package collection. When that
        # probe fails and another one answers, the set is not empty. If it
        # were remembered, writes would read as unsupported for the life of the
        # process, long after the instance came back.
        inner = writable_transport()
        down = True

        def handle(request: httpx.Request) -> httpx.Response:
            if down and "work_packages" in urlparse(str(request.url)).path:
                return httpx.Response(502)
            return inner.handle_request(request)

        built = writer(httpx.MockTransport(handle))
        assert WorkManagementCapability.COMMENT_WORK_ITEM not in built.capabilities
        with pytest.raises(ProviderUnavailableError):
            built.propose_comment(REF, "why this closed")
        down = False
        assert WorkManagementCapability.COMMENT_WORK_ITEM in built.capabilities
