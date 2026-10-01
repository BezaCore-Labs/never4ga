"""Run the WorkManagementProvider contract against the OpenProject adapter.

core/06 section 22: a new backend supplies a fixture and passes the suite. It
does not restate the behaviour, so everything asserted here is asserted once,
for every provider that will ever exist.

The fixtures are the captured API v3 responses in `tests/fixtures/openproject/`;
the live instance is exercised by `tests/integration/test_openproject_live.py`,
which is opt-in and never runs in CI.
"""

from __future__ import annotations

import pytest

from never4ga.adapters.openproject import OpenProjectProvider, OpenProjectWriter
from never4ga.domain.identity import ExternalId
from never4ga.ports.work_management import (
    WorkItem,
    WorkManagementProvider,
    WorkManagementWriter,
)
from tests.contracts.work_management_contract import (
    UnavailableWorkManagementProviderContract,
    WorkManagementProviderContract,
)
from tests.contracts.work_management_writer_contract import WorkManagementWriterContract
from tests.openproject_fixtures import (
    BASE_URL,
    PROJECT_REF,
    fixture_transport,
    offline_transport,
    writable_transport,
)

pytestmark = pytest.mark.contract


def build(transport: object) -> OpenProjectProvider:
    return OpenProjectProvider(
        base_url=BASE_URL,
        token="not-a-real-token",
        connection="work_openproject",
        project_ref=PROJECT_REF,
        transport=transport,  # type: ignore[arg-type]
    )


class TestOpenProjectProvider(WorkManagementProviderContract):
    @pytest.fixture
    def provider(self) -> WorkManagementProvider:
        return build(fixture_transport())

    @pytest.fixture
    def known_item(self, provider: WorkManagementProvider) -> WorkItem:
        found = provider.get_work_item(ExternalId(provider="openproject", value="838"))
        assert found is not None
        return found


class TestUnreachableOpenProjectProvider(UnavailableWorkManagementProviderContract):
    @pytest.fixture
    def provider(self) -> WorkManagementProvider:
        return build(offline_transport())


class TestOpenProjectWriter(WorkManagementWriterContract):
    """The same suite the fake passes, against the real adapter.

    Against `writable_transport`, which models four behaviours of a live
    instance: an unknown field ignored, a stale version refused, a PATCH
    answering with the whole resource, and a comment edit that takes a bare
    string where creating one takes an object.
    """

    @pytest.fixture
    def writer(self) -> WorkManagementWriter:
        return OpenProjectWriter(
            base_url=BASE_URL,
            token="not-a-real-token",
            connection="work_openproject",
            project_ref=PROJECT_REF,
            transport=writable_transport(),
        )

    @pytest.fixture
    def known_item(self, writer: WorkManagementWriter) -> WorkItem:
        found = writer.get_work_item(ExternalId(provider="openproject", value="838"))
        assert found is not None
        return found

    @pytest.fixture
    def project(self) -> str:
        return PROJECT_REF
