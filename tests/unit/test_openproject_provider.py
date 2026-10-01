"""The OpenProject provider: reads, degradation and capability discovery.

Specification:
- details/openproject-adapter.md sections 5 and 12.
- core/05 section 19 -- with the provider unreachable, everything else works.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from never4ga.adapters.openproject import OpenProjectProvider
from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.connections import Connection
from never4ga.domain.identity import ExternalId
from never4ga.errors import ProviderUnavailableError, UnknownWorkItemStatusError
from never4ga.ports.work_management import WorkItemQuery
from tests.openproject_fixtures import (
    BASE_URL,
    PROJECT_REF,
    fixture_transport,
    offline_transport,
    unauthorized_transport,
)


def provider(transport: httpx.MockTransport, **overrides: object) -> OpenProjectProvider:
    arguments: dict[str, object] = {
        "base_url": BASE_URL,
        "token": "not-a-real-token",
        "connection": "work_openproject",
        "project_ref": PROJECT_REF,
        "transport": transport,
    }
    arguments.update(overrides)
    return OpenProjectProvider(**arguments)  # type: ignore[arg-type]


def _transport_without_links(
    *names: str,
    on_collection: bool = False,
) -> httpx.MockTransport:
    """The captured instance, minus some action links.

    OpenProject omits an action link the authenticated user may not perform, so
    removing one is how a token with fewer permissions is simulated.
    """
    inner = fixture_transport()

    def handle(request: httpx.Request) -> httpx.Response:
        response = inner.handle_request(request)
        if response.status_code != 200:
            return response
        payload = json.loads(response.content)
        if not isinstance(payload, dict) or "_embedded" not in payload:
            return response
        targets = [payload] if on_collection else payload.get("_embedded", {}).get("elements", [])
        for target in targets:
            for name in names:
                target.get("_links", {}).pop(name, None)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handle)


class TestConstruction:
    def test_a_connection_supplies_everything_but_the_token(self) -> None:
        # The canonical half comes from the vault, the secret half from the
        # store, and the adapter joins them (core/03 section 16).
        connection = Connection(
            name="work_openproject",
            provider="openproject",
            base_url=BASE_URL,
            project_ref=PROJECT_REF,
        )
        built = OpenProjectProvider.from_connection(
            connection, token="not-a-real-token", transport=fixture_transport()
        )
        assert built.health().available is True

    def test_a_connection_without_a_base_url_is_refused(self) -> None:
        connection = Connection(name="work_openproject", provider="openproject")
        with pytest.raises(ProviderUnavailableError):
            OpenProjectProvider.from_connection(connection, token="not-a-real-token")

    def test_the_token_is_not_in_the_representation(self) -> None:
        built = provider(fixture_transport(), token="s3cret")
        assert "s3cret" not in repr(built)


class TestReads:
    def test_health_reports_the_instance_version(self) -> None:
        health = provider(fixture_transport()).health()
        assert health.available is True
        assert "17.6.0" in health.detail

    def test_resolve_project_names_both_forms_of_the_reference(self) -> None:
        resolved = provider(fixture_transport()).resolve_project()
        assert resolved.identifier == "never4ga"
        assert resolved.numeric_id == "32"
        assert resolved.name == "Never4gA"
        assert resolved.url == f"{BASE_URL}/projects/never4ga"

    def test_get_work_item_reads_one(self) -> None:
        item = provider(fixture_transport()).get_work_item(
            ExternalId(provider="openproject", value="838")
        )
        assert item is not None
        assert item.title.startswith("Milestone 6")

    def test_an_unknown_work_item_is_absent_not_an_error(self) -> None:
        assert (
            provider(fixture_transport()).get_work_item(
                ExternalId(provider="openproject", value="999999")
            )
            is None
        )

    def test_a_reference_from_another_provider_is_never_fetched(self) -> None:
        # An ExternalId is namespaced. Asking OpenProject for a Jira key is a
        # caller's mistake, and the answer is absence rather than a request.
        requested: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, json={})

        built = provider(httpx.MockTransport(handle))
        assert built.get_work_item(ExternalId(provider="jira", value="ABC-1")) is None
        assert requested == []

    def test_search_lists_the_project(self) -> None:
        items = provider(fixture_transport()).search_work_items(WorkItemQuery())
        assert len(items) == 8
        assert all(item.extra["project_ref"] == PROJECT_REF for item in items)

    def test_search_by_status_resolves_the_name_to_an_id(self) -> None:
        items = provider(fixture_transport()).search_work_items(WorkItemQuery(statuses=("New",)))
        assert {item.status for item in items} == {"New"}

    def test_an_unknown_status_name_is_reported_rather_than_matching_nothing(self) -> None:
        """A filter that cannot be applied is not an empty result.

        Dropping the filter would answer a narrow question with every ticket
        there is, and returning `()` would read as "nothing matched". A typo is
        neither: `--status open` against an instance whose statuses are New and
        Closed must not look like an empty backlog.
        """
        with pytest.raises(UnknownWorkItemStatusError) as raised:
            provider(fixture_transport()).search_work_items(
                WorkItemQuery(statuses=("Nonexistent",))
            )
        assert raised.value.unknown == ("Nonexistent",)
        assert "New" in raised.value.known

    def test_it_names_only_the_status_it_could_not_resolve(self) -> None:
        with pytest.raises(UnknownWorkItemStatusError) as raised:
            provider(fixture_transport()).search_work_items(
                WorkItemQuery(statuses=("New", "Nonexistent"))
            )
        assert raised.value.unknown == ("Nonexistent",)

    def test_a_status_name_is_matched_regardless_of_case(self) -> None:
        items = provider(fixture_transport()).search_work_items(WorkItemQuery(statuses=("new",)))
        assert {item.status for item in items} == {"New"}

    def test_search_by_term(self) -> None:
        items = provider(fixture_transport()).search_work_items(WorkItemQuery(terms=("MCP",)))
        assert [item.ref.value for item in items] == ["841"]

    def test_search_respects_a_limit(self) -> None:
        assert len(provider(fixture_transport()).search_work_items(WorkItemQuery(limit=2))) == 2

    def test_search_can_be_bounded_by_change_time(self) -> None:
        future = datetime(2099, 1, 1, tzinfo=UTC)
        assert (
            provider(fixture_transport()).search_work_items(WorkItemQuery(updated_since=future))
            == ()
        )

    def test_a_query_may_name_a_different_project(self) -> None:
        items = provider(fixture_transport(), project_ref=None).search_work_items(
            WorkItemQuery(project=PROJECT_REF)
        )
        assert len(items) == 8

    def test_a_project_search_asks_for_that_project_alone(self) -> None:
        sent: list[str] = []
        inner = fixture_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            query = parse_qs(urlparse(str(request.url)).query)
            if request.url.path.endswith("/work_packages"):
                sent.append(query["filters"][0])
            answered = inner.handler(request)
            assert isinstance(answered, httpx.Response)
            return answered

        provider(httpx.MockTransport(handle)).search_work_items(WorkItemQuery())
        assert sent
        assert all('"subprojectId":{"operator":"!*"' in filters for filters in sent)

    def test_relations_are_read_for_one_item_not_for_a_search(self) -> None:
        built = provider(fixture_transport(relations="relations_populated.json"))
        item = built.get_work_item(ExternalId(provider="openproject", value="838"))
        assert item is not None
        assert len(item.relations) == 2
        assert all(found.relations == () for found in built.search_work_items(WorkItemQuery()))


class TestDegradation:
    def test_health_never_raises_when_the_instance_is_unreachable(self) -> None:
        health = provider(offline_transport()).health()
        assert health.available is False
        assert health.detail

    def test_health_never_raises_when_the_credential_is_rejected(self) -> None:
        health = provider(unauthorized_transport()).health()
        assert health.available is False
        assert "401" in health.detail or "credential" in health.detail.casefold()

    def test_reads_raise_rather_than_returning_nothing(self) -> None:
        # core/05 section 19: degradation is reported, never disguised as an
        # empty result the caller would take for "no open work".
        with pytest.raises(ProviderUnavailableError):
            provider(offline_transport()).search_work_items(WorkItemQuery())

    def test_getting_one_item_from_an_unreachable_instance_raises(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            provider(offline_transport()).get_work_item(
                ExternalId(provider="openproject", value="838")
            )

    def test_capabilities_are_empty_when_nothing_answers(self) -> None:
        assert provider(offline_transport()).capabilities == frozenset()


class TestCapabilityDiscovery:
    def test_capabilities_are_discovered_from_the_instance(self) -> None:
        # section 12: what this instance, version and token can actually do.
        found = provider(fixture_transport()).capabilities
        assert WorkManagementCapability.READ_WORK_ITEMS in found
        assert WorkManagementCapability.SEARCH_WORK_ITEMS in found
        assert WorkManagementCapability.RELATIONS in found
        assert WorkManagementCapability.HIERARCHY in found
        assert WorkManagementCapability.SPRINTS in found

    def test_an_absent_feature_is_not_claimed(self) -> None:
        # No work package on the captured instance carries a custom field, so
        # the adapter must not report the capability.
        assert (
            WorkManagementCapability.CUSTOM_FIELDS not in provider(fixture_transport()).capabilities
        )

    def test_nothing_the_adapter_cannot_do_is_reported(self) -> None:
        # The adapter can create, update and comment. Everything else a work
        # package advertises is outside its reach: every element carries
        # `delete`, `logTime`, `addWatcher` and `addAttachment` links, and none
        # of them may become a capability while nothing can exercise one.
        found = {capability.value for capability in provider(fixture_transport()).capabilities}
        assert not any(
            token in value
            for value in found
            for token in ("delete", "time", "watch", "attach", "webhook")
        )

    def test_discovery_happens_once(self) -> None:
        requests: list[str] = []
        inner = fixture_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return inner.handle_request(request)

        built = provider(httpx.MockTransport(handle))
        first = built.capabilities
        after_first = len(requests)
        assert built.capabilities == first
        assert len(requests) == after_first

    def test_a_failed_discovery_is_not_remembered_as_a_capability_set(self) -> None:
        # An instance that was asleep when it was first asked must not be
        # treated as incapable for the life of the process. Nothing is cached
        # until something answers.
        inner = fixture_transport()
        reachable = False

        def handle(request: httpx.Request) -> httpx.Response:
            if not reachable:
                raise httpx.ConnectError("connection refused", request=request)
            return inner.handle_request(request)

        built = provider(httpx.MockTransport(handle))
        assert built.capabilities == frozenset()
        reachable = True
        assert WorkManagementCapability.READ_WORK_ITEMS in built.capabilities

    def test_a_module_the_instance_does_not_run_is_not_claimed(self) -> None:
        inner = fixture_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/versions"):
                return httpx.Response(404, json={"message": "not found"})
            return inner.handle_request(request)

        found = provider(httpx.MockTransport(handle)).capabilities
        assert WorkManagementCapability.SPRINTS not in found
        assert WorkManagementCapability.READ_WORK_ITEMS in found


class TestProviderVersion:
    def test_an_item_carries_the_instance_version_without_being_asked_twice(self) -> None:
        # section 8 lists `provider_version`. Whether a field is present must
        # not depend on whether the caller happened to run `health` first.
        item = provider(fixture_transport()).get_work_item(
            ExternalId(provider="openproject", value="838")
        )
        assert item is not None
        assert item.extra["provider_version"] == "17.6.0"

    def test_the_version_is_read_once(self) -> None:
        roots: list[str] = []
        inner = fixture_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v3":
                roots.append(str(request.url))
            return inner.handle_request(request)

        built = provider(httpx.MockTransport(handle))
        built.search_work_items(WorkItemQuery())
        built.search_work_items(WorkItemQuery())
        assert len(roots) == 1

    def test_an_unreachable_root_costs_the_version_not_the_read(self) -> None:
        # A version is a nicety. Failing a read because the root endpoint was
        # slow would trade a whole answer for a label.
        inner = fixture_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v3":
                return httpx.Response(500, json={"message": "boom"})
            return inner.handle_request(request)

        item = provider(httpx.MockTransport(handle)).get_work_item(
            ExternalId(provider="openproject", value="838")
        )
        assert item is not None
        assert "provider_version" not in item.extra


class TestBounds:
    def test_a_limit_of_zero_asks_for_nothing(self) -> None:
        requested: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, json={"total": 0, "_embedded": {"elements": []}})

        built = provider(httpx.MockTransport(handle))
        assert built.search_work_items(WorkItemQuery(limit=0)) == ()
        assert requested == []

    def test_an_assignee_filtered_search_stays_bounded(self) -> None:
        # The API filters assignees by principal id, which this adapter does
        # not resolve, so the filtering happens here -- and a client-side
        # filter must not turn a bounded read into "fetch the whole tracker".
        pages: list[int] = []
        inner = fixture_transport()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/work_packages"):
                pages.append(int(request.url.params.get("pageSize", 0)))
            return inner.handle_request(request)

        built = provider(httpx.MockTransport(handle))
        built.search_work_items(WorkItemQuery(assignees=("Ada Example",), limit=1))
        assert pages and max(pages) <= 100
