"""A write the next read can see.

`CachingWorkManagementProvider.get_work_item` answers from the cache while the
entry is fresh. An applied write therefore forgets the cached item, or the next
plain read would serve the copy from before the write as current.

This is worse than a stale read: an agent that writes and reads back to confirm
would see its own write missing and conclude it failed. The cache forgets
rather than updates, because an applied mutation returns a fresh item for some
actions and nothing for others, and a cache holding a half-item would be a
subtler version of the same error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from never4ga.adapters.fakes import (
    FakeRepositoryLocator,
    FakeWorkManagementWriter,
    InMemoryDocumentStore,
    InMemorySecretStore,
    InMemoryTrackerCache,
    InMemoryWorkspaceMappingStore,
)
from never4ga.domain.connections import Connection
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId, ExternalId, WorkItemKey
from never4ga.domain.work_policy import WritePolicy
from never4ga.errors import StructuredError
from never4ga.ports.tracker_cache import CachedWorkItem
from never4ga.ports.work_management import WorkItem
from never4ga.services.connections import secret_ref_for
from never4ga.services.work_writing import Bound, WorkWriteService
from never4ga.services.workspaces import WorkspaceService

WORKSPACE = ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbb6")
WORKSPACE_PATH = VaultPath.parse("10_Workspaces/Example/workspace.md")
REPOSITORY = PurePath("/home/someone/Projects/example")
CONNECTION = Connection(
    name="work_openproject",
    provider="fake-pm",
    base_url="https://pm.example.dev",
    project_ref="example",
)
REF = ExternalId(provider="fake-pm", value="1167")
ITEM = WorkItem(ref=REF, title="A deep pass", status="New")

#: The key a targeted read looks under. Built the way the *reader* builds it,
#: not the writer, because forgetting a different key would pass its own test
#: and change nothing.
KEY = WorkItemKey(
    connection=CONNECTION.name,
    project_ref=CONNECTION.project_ref or "",
    external_id=REF,
)


def workspace_document() -> StoredDocument:
    return StoredDocument(
        concept_id=WORKSPACE,
        path=WORKSPACE_PATH,
        frontmatter={
            "type": "workspace",
            "id": str(WORKSPACE),
            "schema": "never4ga/0.1",
            "title": "Example",
            "created_at": "2026-08-26T00:00:00Z",
            "work_management": {
                "mode": "external",
                "connection": CONNECTION.name,
                "provider": "fake-pm",
                "project_ref": "example",
                "sync_policy": "read_write",
            },
        },
        body="# Example\n",
    )


def workspaces() -> WorkspaceService:
    service = WorkspaceService(InMemoryWorkspaceMappingStore(), FakeRepositoryLocator([REPOSITORY]))
    service.map(workspace_id=WORKSPACE, workspace_path=WORKSPACE_PATH, repository_root=REPOSITORY)
    return service


def cached(cache: InMemoryTrackerCache) -> None:
    cache.put(CachedWorkItem(key=KEY, item=ITEM, fetched_at=datetime.now(UTC)))


def service(cache: InMemoryTrackerCache) -> WorkWriteService:
    secrets = InMemorySecretStore()
    secrets.set(secret_ref_for(CONNECTION), "a-token")
    documents = InMemoryDocumentStore()
    documents.put(workspace_document())

    def factory(connection: Connection) -> Any:
        return FakeWorkManagementWriter([ITEM])

    return WorkWriteService(
        documents=documents,
        workspaces=workspaces(),
        factory=factory,
        cache=cache,
    )


def bound(*, applying: bool) -> Bound:
    return Bound(
        workspace=workspace_document(),
        connection=CONNECTION,
        writer=FakeWorkManagementWriter([ITEM]),
        policy=WritePolicy(mode="external", sync_policy="read_write", applying=applying),
    )


class TestAnAppliedWriteIsVisibleToTheNextRead:
    def test_an_applied_update_forgets_the_cached_item(self) -> None:
        cache = InMemoryTrackerCache()
        cached(cache)
        service(cache).update(bound(applying=True), REF.value, {"status": "In progress"})
        assert cache.get(KEY) is None

    def test_an_applied_comment_forgets_it_too(self) -> None:
        # A comment returns no item, and the tracker's `updated_at` moves
        # anyway. Forgetting covers both without the write path having to know
        # which actions return what.
        cache = InMemoryTrackerCache()
        cached(cache)
        service(cache).comment(bound(applying=True), REF.value, "a note")
        assert cache.get(KEY) is None

    def test_the_key_forgotten_is_the_key_a_read_looks_under(self) -> None:
        # Forgetting a key nobody reads would leave stale reads in place and the
        # test green. `project_ref` comes from the connection,
        # because that is what `_reference_key` uses -- a workspace may declare
        # a different one, and using that would miss.
        cache = InMemoryTrackerCache()
        cached(cache)
        service(cache).update(bound(applying=True), REF.value, {"status": "Closed"})
        assert not cache.entries(CONNECTION.name, CONNECTION.project_ref or "")


class TestNothingElseIsForgotten:
    def test_a_proposal_that_was_not_applied_keeps_the_cache(self) -> None:
        # A dry run changed nothing on the tracker, so the cached copy is still
        # exactly as true as it was.
        cache = InMemoryTrackerCache()
        cached(cache)
        service(cache).update(bound(applying=False), REF.value, {"status": "In progress"})
        assert cache.get(KEY) is not None

    def test_another_item_is_untouched(self) -> None:
        cache = InMemoryTrackerCache()
        cached(cache)
        other = WorkItemKey(
            connection=CONNECTION.name,
            project_ref=CONNECTION.project_ref or "",
            external_id=ExternalId(provider="fake-pm", value="999"),
        )
        cache.put(
            CachedWorkItem(
                key=other,
                item=WorkItem(ref=other.external_id, title="Other", status="New"),
                fetched_at=datetime.now(UTC),
            )
        )
        service(cache).update(bound(applying=True), REF.value, {"status": "Closed"})
        assert cache.get(other) is not None

    def test_a_service_with_no_cache_still_writes(self) -> None:
        # Every root wires the cache, but the parameter is optional and a test
        # double may not have one. A write must not depend on it existing.
        secrets = InMemorySecretStore()
        secrets.set(secret_ref_for(CONNECTION), "a-token")
        documents = InMemoryDocumentStore()
        documents.put(workspace_document())
        writer = WorkWriteService(
            documents=documents,
            workspaces=workspaces(),
            factory=lambda connection: FakeWorkManagementWriter([ITEM]),
        )
        outcome = writer.update(bound(applying=True), REF.value, {"status": "Closed"})
        assert not isinstance(outcome, StructuredError)
        assert outcome["applied"] is True
