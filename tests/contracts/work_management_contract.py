"""WorkManagementProvider contract.

Specification:
- details/external-work-management-architecture.md -- generic provider interface.
- core/07 Stage F -- operational state is read mechanically from the API, not inferred.
- core/05 section 19 -- OpenProject offline must still leave vault search working.
"""

from __future__ import annotations

import pytest

from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ConceptId, ExternalId
from never4ga.errors import ProviderUnavailableError
from never4ga.ports.work_management import (
    WorkItem,
    WorkItemQuery,
    WorkManagementProvider,
)


class WorkManagementProviderContract:
    @pytest.fixture
    def provider(self) -> WorkManagementProvider:
        raise NotImplementedError("supply a WorkManagementProvider fixture")

    @pytest.fixture
    def known_item(self, provider: WorkManagementProvider) -> WorkItem:
        raise NotImplementedError("supply a work item known to the provider")

    def test_declares_read_capabilities(self, provider: WorkManagementProvider) -> None:
        assert WorkManagementCapability.READ_WORK_ITEMS in provider.capabilities

    def test_get_by_reference(self, provider: WorkManagementProvider, known_item: WorkItem) -> None:
        found = provider.get_work_item(known_item.ref)
        assert found is not None
        assert found.title == known_item.title

    def test_get_unknown_reference_returns_none(self, provider: WorkManagementProvider) -> None:
        assert (
            provider.get_work_item(ExternalId(provider="fake-pm", value="does-not-exist")) is None
        )

    def test_work_items_carry_provider_identity_not_canonical_identity(
        self, provider: WorkManagementProvider, known_item: WorkItem
    ) -> None:
        # core/06 section 3: a PM identifier is never Never4gA canonical identity.
        assert isinstance(known_item.ref, ExternalId)
        assert not issubclass(ExternalId, ConceptId)

    def test_search_by_status(self, provider: WorkManagementProvider, known_item: WorkItem) -> None:
        assert known_item.status is not None
        results = provider.search_work_items(WorkItemQuery(statuses=(known_item.status,)))
        assert known_item.ref in {item.ref for item in results}

    def test_search_with_no_filters_lists_items(self, provider: WorkManagementProvider) -> None:
        assert provider.search_work_items(WorkItemQuery())

    def test_search_limit_is_respected(self, provider: WorkManagementProvider) -> None:
        assert len(provider.search_work_items(WorkItemQuery(limit=1))) <= 1

    def test_health_reports_availability_without_raising(
        self, provider: WorkManagementProvider
    ) -> None:
        health = provider.health()
        assert health.available is True

    def test_reading_is_a_role_that_demands_nothing_of_writing(
        self, provider: WorkManagementProvider
    ) -> None:
        # core/03 section 24: a provider lacking a feature reports the
        # capability unavailable rather than implementing it emptily.
        #
        # The assertion is about the *port*, deliberately. A writer is also a
        # reader and legitimately passes this suite, so "this instance is not a
        # writer" is the wrong universal rule. What must hold for every reader
        # is that satisfying this role costs nothing on the other:
        # `WorkManagementProvider` names no write member.
        assert not {"propose_update", "propose_create", "propose_comment", "apply"} & set(
            WorkManagementProvider.__protocol_attrs__  # type: ignore[attr-defined]
        )
        assert isinstance(provider, WorkManagementProvider)


class UnavailableWorkManagementProviderContract:
    """An unreachable provider must fail explicitly, never silently return nothing."""

    @pytest.fixture
    def provider(self) -> WorkManagementProvider:
        raise NotImplementedError("supply an unavailable WorkManagementProvider fixture")

    def test_health_reports_unavailable_without_raising(
        self, provider: WorkManagementProvider
    ) -> None:
        health = provider.health()
        assert health.available is False
        assert health.detail

    def test_reads_raise_provider_unavailable(self, provider: WorkManagementProvider) -> None:
        with pytest.raises(ProviderUnavailableError):
            provider.search_work_items(WorkItemQuery())
