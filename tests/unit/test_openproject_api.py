"""The OpenProject API v3 client: paging, filters and failure.

Specification:
- details/openproject-adapter.md section 2 -- API v3 is the integration.
- details/openproject-adapter.md section 5 -- the read operations.

The paging tests matter most. `offset` on an OpenProject collection is a
1-based page number, and a paginator that treats it as an element offset
re-fetches overlapping windows while passing every single-page test.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from never4ga.adapters.openproject.api import OpenProjectApi, work_package_filters
from never4ga.errors import ProviderUnavailableError
from never4ga.ports.work_management import WorkItemQuery
from tests.openproject_fixtures import (
    BASE_URL,
    captured_pages_transport,
    fixture_transport,
    offline_transport,
    unauthorized_transport,
)


def api(transport: httpx.MockTransport) -> OpenProjectApi:
    return OpenProjectApi(BASE_URL, token="not-a-real-token", transport=transport)


class TestPaging:
    def test_offset_is_a_page_number_not_an_element_offset(self) -> None:
        # The transport serves only the captured pages and refuses anything
        # else, so a paginator stepping by pageSize fails here rather than in
        # production against a collection too large for one page.
        client = api(captured_pages_transport())
        elements = client.collection("/api/v3/projects/never4ga/work_packages", page_size=3)
        assert [element["id"] for element in elements] == [838, 839, 840, 841, 842, 843, 844, 845]

    def test_paging_stops_at_the_reported_total(self) -> None:
        client = api(captured_pages_transport())
        elements = client.collection("/api/v3/projects/never4ga/work_packages", page_size=3)
        assert len(elements) == len({element["id"] for element in elements})

    def test_a_limit_stops_early(self) -> None:
        client = api(fixture_transport())
        elements = client.collection(
            "/api/v3/projects/never4ga/work_packages", page_size=3, limit=2
        )
        assert len(elements) == 2


class TestRequests:
    def test_get_returns_the_decoded_body(self) -> None:
        client = api(fixture_transport())
        assert client.get("/api/v3")["coreVersion"] == "17.6.0"

    def test_a_missing_resource_is_absence_not_failure(self) -> None:
        client = api(fixture_transport())
        assert client.get_optional("/api/v3/work_packages/999999") is None

    def test_the_token_travels_as_basic_auth_named_apikey(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"_type": "Root"})

        client = OpenProjectApi(BASE_URL, token="s3cret", transport=httpx.MockTransport(handle))
        client.get("/api/v3")
        assert seen[0].headers["authorization"].startswith("Basic ")

    def test_a_redirect_within_the_instance_is_followed(self) -> None:
        # `/work_packages/{id}/relations` answers 308 to `/relations?filters=...`.
        # Not following it returns HTML, and the captured fixtures cannot show
        # it because a fixture is a body.
        def handle(request: httpx.Request) -> httpx.Response:
            if "filters" not in str(request.url):
                return httpx.Response(308, headers={"Location": "/api/v3/relations?filters=%5B%5D"})
            return httpx.Response(200, json={"total": 0, "_embedded": {"elements": []}})

        client = api(httpx.MockTransport(handle))
        assert client.get("/api/v3/work_packages/838/relations")["total"] == 0

    def test_a_redirect_off_the_instance_is_refused(self) -> None:
        # The client carries a credential on every request. Following a
        # redirect to another host would hand it to whoever asked.
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "https://elsewhere.example/api/v3"})

        client = OpenProjectApi(BASE_URL, token="s3cret", transport=httpx.MockTransport(handle))
        with pytest.raises(ProviderUnavailableError) as raised:
            client.get("/api/v3")
        assert "elsewhere.example" in str(raised.value)
        assert "s3cret" not in str(raised.value)

    def test_a_redirect_loop_ends(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(308, headers={"Location": "/api/v3/round-again"})

        client = api(httpx.MockTransport(handle))
        with pytest.raises(ProviderUnavailableError):
            client.get("/api/v3")

    def test_a_refused_connection_is_provider_unavailable(self) -> None:
        client = api(offline_transport())
        with pytest.raises(ProviderUnavailableError):
            client.get("/api/v3")

    def test_a_rejected_credential_is_provider_unavailable(self) -> None:
        client = api(unauthorized_transport())
        with pytest.raises(ProviderUnavailableError) as raised:
            client.get("/api/v3")
        assert "s3cret" not in str(raised.value)

    def test_an_error_never_carries_the_token(self) -> None:
        client = OpenProjectApi(BASE_URL, token="s3cret", transport=unauthorized_transport())
        with pytest.raises(ProviderUnavailableError) as raised:
            client.get("/api/v3")
        assert "s3cret" not in repr(raised.value)


class TestFilters:
    def test_no_status_filter_asks_for_every_status(self) -> None:
        # OpenProject's default is `status: open`, so an empty query would
        # silently exclude closed items. An empty Never4gA query means no
        # filter at all, and the adapter has to say so.
        filters = work_package_filters(WorkItemQuery(), status_ids=())
        assert {"status": {"operator": "*", "values": []}} in filters

    def test_statuses_are_sent_as_ids(self) -> None:
        filters = work_package_filters(WorkItemQuery(statuses=("New",)), status_ids=("1",))
        assert {"status": {"operator": "=", "values": ["1"]}} in filters

    def test_terms_become_a_search_filter(self) -> None:
        filters = work_package_filters(WorkItemQuery(terms=("mcp",)), status_ids=())
        assert {"search": {"operator": "**", "values": ["mcp"]}} in filters

    def test_a_project_listing_excludes_its_subprojects(self) -> None:
        # OpenProject lists a project's subprojects' work packages with its
        # own, so a parent project would answer with every child's items. A
        # workspace's tracker is its own project.
        filters = work_package_filters(WorkItemQuery(), status_ids=(), own_project_only=True)
        assert {"subprojectId": {"operator": "!*", "values": []}} in filters

    def test_an_unscoped_listing_has_no_project_to_narrow_to(self) -> None:
        filters = work_package_filters(WorkItemQuery(), status_ids=())
        assert not any("subprojectId" in entry for entry in filters)

    def test_filters_reach_the_server_as_one_json_parameter(self) -> None:
        seen: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            query = parse_qs(urlparse(str(request.url)).query)
            seen.append(query["filters"][0])
            return httpx.Response(
                200,
                json={"total": 0, "count": 0, "_embedded": {"elements": []}},
            )

        client = api(httpx.MockTransport(handle))
        client.collection(
            "/api/v3/projects/never4ga/work_packages",
            params={"filters": json.dumps([{"status": {"operator": "*", "values": []}}])},
        )
        assert json.loads(seen[0]) == [{"status": {"operator": "*", "values": []}}]
