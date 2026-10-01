"""A workspace's work signals are about *that workspace's* project.

`core/03` §15 puts the project declaration on the workspace. The connection is
the *machine's* half, where the server is and which token opens it, and one
connection may serve every workspace. Taking the project from the connection
would make every workspace report one project's work.

Both the reported project and the query must come from the declaration. The
query matters more: :class:`WorkItemQuery` has a ``project`` field, and the
signal provider must fill it in from the declaration just as
:class:`WorkReadService` does.

Every other lane of the startup pack is structure and history; the work lane is
the only one that says what is happening *now*. A pack that confidently lists
another project's tickets is worse than one that lists none.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from never4ga.adapters.fakes import InMemoryDocumentStore
from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.connections import Connection
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest
from never4ga.ports.work_management import (
    ProviderHealth,
    WorkItem,
    WorkItemQuery,
    WorkManagementProvider,
)
from never4ga.services.connections import ConnectionRegistry
from never4ga.services.work_signals import WorkManagementSignalProvider

CONNECTION = "work_openproject"

#: The connection's own project. One connection serves the whole machine, so
#: this value must not leak into every workspace's pack.
CONNECTION_PROJECT = "never4ga"


class RecordingProvider:
    """A provider that remembers what it was asked for."""

    def __init__(self) -> None:
        self.queries: list[WorkItemQuery] = []

    @property
    def provider_id(self) -> str:
        return "openproject"

    @property
    def capabilities(self) -> frozenset[WorkManagementCapability]:
        return frozenset()

    def get_work_item(self, ref: Any, *, refresh: bool = False) -> WorkItem | None:
        return None

    def search_work_items(self, query: WorkItemQuery) -> Sequence[WorkItem]:
        self.queries.append(query)
        return ()

    def health(self) -> ProviderHealth:
        return ProviderHealth(available=True)


def workspace(name: str, **work_management: str) -> StoredDocument:
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(f"10_Workspaces/{name}/workspace.md"),
        frontmatter={
            "type": "workspace",
            "schema": "never4ga/0.1",
            "title": name,
            "workspace_type": "product",
            "work_management": {
                "mode": "external",
                "connection": CONNECTION,
                "provider": "openproject",
                "sync_policy": "read",
                **work_management,
            },
        },
        body="",
    )


def integration() -> StoredDocument:
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(f"50_System/Integrations/{CONNECTION}.md"),
        frontmatter={
            "type": "integration",
            "schema": "never4ga/0.1",
            "title": "Work OpenProject",
            "connection": CONNECTION,
            "provider": "openproject",
            "base_url": "https://openproject.example",
            "project_ref": CONNECTION_PROJECT,
        },
        body="",
    )


@pytest.fixture
def provider() -> RecordingProvider:
    return RecordingProvider()


def signals_for(manifest: StoredDocument, provider: RecordingProvider) -> dict[str, Any]:
    store = InMemoryDocumentStore()
    store.put(integration())
    store.put(manifest)

    def factory(_connection: Connection) -> WorkManagementProvider:
        return provider

    collected = WorkManagementSignalProvider(
        documents=store,
        connections=ConnectionRegistry(documents=store),
        factory=factory,
    ).collect(
        ContextRequest(
            scope=ScopeRequest(),
            depth=ContextDepth.STARTUP,
            budget=ContextBudget(max_items=20, max_characters=20_000),
        ),
        ResolvedScope(
            workspace_id=manifest.concept_id,
            workspace_path=manifest.path,
            reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
        ),
    )
    return {signal.kind: signal.value for signal in collected}


class TestTheProjectComesFromTheWorkspace:
    def test_the_reported_project_is_the_workspaces(self, provider: RecordingProvider) -> None:
        manifest = workspace("Acme Infra", project_ref="acme-infrastructure")
        assert signals_for(manifest, provider)["work.project"] == "acme-infrastructure"

    def test_and_so_are_the_items_it_asked_for(self, provider: RecordingProvider) -> None:
        """The query asks for the workspace's project, not only the label.

        Labelling it correctly while querying the connection's project would
        name the right project over the wrong project's tickets.
        """
        signals_for(workspace("Acme Infra", project_ref="acme-infrastructure"), provider)
        (query,) = provider.queries
        assert query.project == "acme-infrastructure"

    def test_two_workspaces_on_one_connection_get_their_own(
        self, provider: RecordingProvider
    ) -> None:
        """Two workspaces sharing one connection each get their own project.

        A single workspace whose project is the connection's default cannot
        tell the two sources apart; a second workspace can.
        """
        first = signals_for(workspace("Alpha", project_ref="alpha"), provider)
        second = signals_for(workspace("Beta", project_ref="beta"), provider)
        assert (first["work.project"], second["work.project"]) == ("alpha", "beta")
        assert [q.project for q in provider.queries] == ["alpha", "beta"]


class TestTheConnectionIsStillTheFallback:
    def test_a_workspace_that_declares_none_uses_the_connections(
        self, provider: RecordingProvider
    ) -> None:
        """`core/03` §15 makes the declaration the authority, not the only source.

        A workspace naming a connection and no project is asking for the
        connection's, which is what `WorkReadService` does.
        """
        signals = signals_for(workspace("Gamma"), provider)
        assert signals["work.project"] == CONNECTION_PROJECT
        assert provider.queries[0].project == CONNECTION_PROJECT

    def test_an_empty_declaration_is_the_same_as_none(self, provider: RecordingProvider) -> None:
        assert signals_for(workspace("Delta", project_ref=""), provider)["work.project"] == (
            CONNECTION_PROJECT
        )


class TestItAgreesWithTheReadPath:
    def test_both_resolve_a_project_through_one_function(self) -> None:
        """Two copies of one rule would drift apart.

        `WorkReadService.bind` and the signal provider resolve a project through
        one function, and this fails if either module grows its own again.
        """
        import inspect

        from never4ga.services import work_reading, work_signals

        for module in (work_reading, work_signals):
            assert "project_ref_for(" in inspect.getsource(module), (
                f"{module.__name__} resolves a project some other way"
            )
