"""Tracker state as Context Pack signals (core/07 Stage F).

Specification:
- core/07 Stage F -- current ticket, status, assignee, priority, milestone,
  updated_at, relationships, read from the API rather than inferred.
- core/07 section 15 -- startup stays bounded and needs no model call.
- core/03 section 15 -- `work_management` on the workspace, and what each
  `sync_policy` permits.
- core/05 section 19 -- an unreachable tracker degrades; nothing depends on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any

import pytest

from never4ga.adapters.fakes import (
    FakeWorkManagementProvider,
    InMemoryDocumentStore,
    InMemoryTrackerCache,
)
from never4ga.domain.connections import Connection
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId, ExternalId, WorkItemKey
from never4ga.domain.provenance import AcquisitionStage
from never4ga.domain.scope import ResolvedScope, ScopeRequest
from never4ga.domain.signals import MAX_SIGNAL_TEXT_LENGTH, ContextSignal
from never4ga.errors import ProviderUnavailableError, WorkItemNotFoundError
from never4ga.ports.tracker_cache import CachedWorkItem
from never4ga.ports.work_management import WorkItem, WorkManagementProvider
from never4ga.services.connections import ConnectionRegistry
from never4ga.services.work_signals import MAX_ITEMS_READ, WorkManagementSignalProvider

WORKSPACE = "10_Workspaces/BezaCore-Labs/Workspaces/Never4gA/workspace.md"
CONNECTION = "work_openproject"
PROJECT = "never4ga"
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def workspace_document(workspace_id: ConceptId, **work_management: Any) -> StoredDocument:
    frontmatter: dict[str, Any] = {
        "type": "workspace",
        "schema": "never4ga/0.1",
        "title": "Never4gA",
    }
    if work_management:
        frontmatter["work_management"] = work_management
    return StoredDocument(
        concept_id=workspace_id,
        path=VaultPath.parse(WORKSPACE),
        frontmatter=frontmatter,
        body="",
    )


def integration_document() -> StoredDocument:
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
            "project_ref": PROJECT,
        },
        body="",
    )


def ticket(
    value: str,
    *,
    status: str = "New",
    assignee: str | None = None,
    updated: int = 0,
    title: str = "A work package",
) -> WorkItem:
    return WorkItem(
        ref=ExternalId(provider="fake-pm", value=value),
        title=title,
        status=status,
        assignee=assignee,
        priority="Normal",
        milestone="v0.1",
        updated_at=NOW - timedelta(hours=updated),
        url=f"https://openproject.example/work_packages/{value}",
        relations=(ExternalId(provider="fake-pm", value="999"),),
        extra={"connection": CONNECTION, "project_ref": PROJECT, "type": "Epic"},
    )


@pytest.fixture
def workspace_id() -> ConceptId:
    return ConceptId.new()


@pytest.fixture
def store(workspace_id: ConceptId) -> InMemoryDocumentStore:
    documents = InMemoryDocumentStore()
    documents.put(workspace_document(workspace_id, mode="external", connection=CONNECTION))
    documents.put(integration_document())
    return documents


@pytest.fixture
def scope(workspace_id: ConceptId) -> ResolvedScope:
    from never4ga.domain.provenance import AcquisitionReason, ReasonCode

    return ResolvedScope(
        workspace_id=workspace_id,
        workspace_path=VaultPath.parse(WORKSPACE),
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
        repository_root=PurePath("/home/user/Projects/never4ga"),
    )


def request(depth: ContextDepth = ContextDepth.STARTUP) -> ContextRequest:
    return ContextRequest(
        scope=ScopeRequest(),
        depth=depth,
        budget=ContextBudget(max_items=20, max_characters=20_000),
    )


def build(
    store: InMemoryDocumentStore,
    items: Sequence[WorkItem] = (),
    *,
    available: bool = True,
    cache: InMemoryTrackerCache | None = None,
    provider: WorkManagementProvider | None = None,
) -> WorkManagementSignalProvider:
    built = provider or FakeWorkManagementProvider(items, available=available)

    def factory(connection: Connection) -> WorkManagementProvider:
        return built

    return WorkManagementSignalProvider(
        documents=store,
        connections=ConnectionRegistry(documents=store),
        factory=factory,
        cache=cache,
        clock=lambda: NOW,
    )


def by_kind(signals: Sequence[ContextSignal]) -> dict[str, Any]:
    return {signal.kind: signal.value for signal in signals}


class TestDeclining:
    def test_a_workspace_with_no_declaration_costs_nothing(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        documents = InMemoryDocumentStore()
        documents.put(workspace_document(workspace_id))

        def factory(connection: Connection) -> WorkManagementProvider:
            raise AssertionError("no connection should have been built")

        provider = WorkManagementSignalProvider(
            documents=documents,
            connections=ConnectionRegistry(documents=documents),
            factory=factory,
        )
        assert provider.collect(request(), scope) == ()

    def test_a_native_workspace_is_not_asked_about_a_tracker(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        # core/03 section 15: `native` means Never4gA's own Tasks/ is
        # authoritative, so there is no external tracker to read.
        documents = InMemoryDocumentStore()
        documents.put(workspace_document(workspace_id, mode="native"))
        assert build(documents, [ticket("1")]).collect(request(), scope) == ()

    def test_a_reference_policy_does_not_ingest(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        # core/03 section 15: `reference` means Never4gA "knows the project
        # mapping but does not automatically ingest/synchronize ticket data".
        # Startup is automatic, so it must not.
        documents = InMemoryDocumentStore()
        documents.put(
            workspace_document(
                workspace_id, mode="external", connection=CONNECTION, sync_policy="reference"
            )
        )
        documents.put(integration_document())
        assert build(documents, [ticket("1")]).collect(request(), scope) == ()

    def test_a_declaration_naming_no_connection_is_not_guessed_at(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        documents = InMemoryDocumentStore()
        documents.put(workspace_document(workspace_id, mode="external"))
        assert build(documents, [ticket("1")]).collect(request(), scope) == ()

    def test_a_connection_the_vault_does_not_define_is_not_fatal(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        # A workspace naming a connection nobody wrote down is a configuration
        # fault. `doctor` is where that is reported; a startup pack degrades.
        documents = InMemoryDocumentStore()
        documents.put(workspace_document(workspace_id, mode="external", connection="nowhere"))
        assert build(documents, [ticket("1")]).collect(request(), scope) == ()

    def test_it_supports_every_depth(self, store: InMemoryDocumentStore) -> None:
        provider = build(store)
        assert all(provider.supports(request(depth)) for depth in ContextDepth)


class TestSignals:
    def test_the_connection_and_project_are_named(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        found = by_kind(build(store, [ticket("1")]).collect(request(), scope))
        assert found["work.connection"] == CONNECTION
        assert found["work.project"] == PROJECT

    def test_counts_are_reported_by_status(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        items = [
            ticket("1", status="New"),
            ticket("2", status="New"),
            ticket("3", status="In progress"),
        ]
        found = by_kind(build(store, items).collect(request(), scope))
        assert found["work.by_status"] == {"In progress": 1, "New": 2}
        assert found["work.counted"] == 3

    def test_items_carry_stage_f_fields(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        found = by_kind(
            build(store, [ticket("1", assignee="Ada Example")]).collect(request(), scope)
        )
        assert found["work.items"] == [
            {
                "id": "1",
                "title": "A work package",
                "status": "New",
                "assignee": "Ada Example",
                "priority": "Normal",
                "milestone": "v0.1",
                "updated_at": "2026-08-26T09:00:00+00:00",
                "relations": 1,
                "url": "https://openproject.example/work_packages/1",
            }
        ]

    def test_an_unset_field_is_left_out_rather_than_emptied(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        found = by_kind(build(store, [ticket("1")]).collect(request(), scope))
        assert "assignee" not in found["work.items"][0]

    def test_the_most_recently_changed_come_first(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        items = [ticket("old", updated=48), ticket("new", updated=1), ticket("mid", updated=10)]
        found = by_kind(build(store, items).collect(request(), scope))
        assert [item["id"] for item in found["work.items"]] == ["new", "mid", "old"]

    def test_every_signal_is_mechanical(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # core/07 section 15: startup requires no Never4gA-owned model call, and
        # a signal is a fact rather than a sentence.
        signals = build(store, [ticket("1")]).collect(request(), scope)
        assert signals
        for signal in signals:
            assert signal.reason.is_mechanical
            assert signal.reason.stage is AcquisitionStage.EXTERNAL_STATE
            assert signal.provider_id == "work_management"

    def test_a_long_title_is_clipped_rather_than_dropped(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        found = by_kind(build(store, [ticket("1", title="x" * 400)]).collect(request(), scope))
        assert len(found["work.items"][0]["title"]) <= MAX_SIGNAL_TEXT_LENGTH


class TestBounds:
    def test_startup_lists_few_items(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        items = [ticket(str(number), updated=number) for number in range(40)]
        found = by_kind(build(store, items).collect(request(ContextDepth.STARTUP), scope))
        assert len(found["work.items"]) == 5

    def test_deeper_requests_list_more(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        items = [ticket(str(number), updated=number) for number in range(40)]
        startup = by_kind(build(store, items).collect(request(ContextDepth.STARTUP), scope))
        deep = by_kind(build(store, items).collect(request(ContextDepth.DEEP), scope))
        assert len(deep["work.items"]) > len(startup["work.items"])

    def test_counts_still_cover_everything_that_was_read(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # Trimming the list must not trim the summary: "five of forty" is a
        # different fact from "five".
        items = [ticket(str(number), updated=number) for number in range(40)]
        found = by_kind(build(store, items).collect(request(), scope))
        assert found["work.counted"] == 40

    def test_a_read_that_did_not_fill_its_ceiling_says_nothing_about_truncation(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        items = [ticket(str(number), updated=number) for number in range(40)]
        found = by_kind(build(store, items).collect(request(), scope))
        assert "work.truncated" not in found

    def test_a_read_that_filled_its_ceiling_says_so(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # Without this, work.counted reads as a real total when it is a floor,
        # and by_status is a slice presented as a summary.
        items = [ticket(str(number), updated=number) for number in range(MAX_ITEMS_READ)]
        found = by_kind(build(store, items).collect(request(), scope))
        assert found["work.truncated"] is True
        assert found["work.counted"] == MAX_ITEMS_READ


class TestDegradation:
    def test_an_unreachable_tracker_degrades_the_provider(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # core/05 section 19: the assembler catches this and records the
        # provider as degraded. The pack still assembles.
        with pytest.raises(ProviderUnavailableError):
            build(store, [ticket("1")], available=False).collect(request(), scope)

    def test_what_was_cached_answers_when_the_tracker_cannot(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        cache = InMemoryTrackerCache()
        cache.put(
            CachedWorkItem(
                key=WorkItemKey(
                    connection=CONNECTION,
                    project_ref=PROJECT,
                    external_id=ExternalId(provider="fake-pm", value="1"),
                ),
                item=ticket("1"),
                fetched_at=NOW - timedelta(hours=3),
            )
        )
        found = by_kind(build(store, available=False, cache=cache).collect(request(), scope))
        assert found["work.live"] is False
        assert found["work.as_of"] == "2026-08-26T06:00:00+00:00"
        assert [item["id"] for item in found["work.items"]] == ["1"]

    def test_a_live_read_says_so(self, store: InMemoryDocumentStore, scope: ResolvedScope) -> None:
        found = by_kind(build(store, [ticket("1")]).collect(request(), scope))
        assert found["work.live"] is True
        assert found["work.as_of"] == "2026-08-26T09:00:00+00:00"

    def test_an_empty_cache_does_not_disguise_an_unreachable_tracker(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # Nothing cached and nothing reachable is not "no open work".
        with pytest.raises(ProviderUnavailableError):
            build(store, available=False, cache=InMemoryTrackerCache()).collect(request(), scope)


def focus_on(reference: str | None = None, *, refresh: bool = False) -> ContextRequest:
    return ContextRequest(
        scope=ScopeRequest(),
        depth=ContextDepth.FOCUSED,
        budget=ContextBudget(max_items=20, max_characters=20_000),
        work_item=reference,
        refresh=refresh,
    )


class TestOneWorkItem:
    def test_a_named_reference_becomes_the_current_item(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # core/07 Stage F's first signal is "current ticket". It is a reference
        # on the request rather than a second path through assembly.
        found = by_kind(build(store, [ticket("1"), ticket("2")]).collect(focus_on("2"), scope))
        assert found["work.current"]["id"] == "2"

    def test_the_current_item_carries_its_relations_in_full(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # A listed item says how many relations it has; the one being worked on
        # says which, because that is the question `--ticket` was asked.
        found = by_kind(build(store, [ticket("1")]).collect(focus_on("1"), scope))
        assert found["work.current"]["relations"] == ["999"]

    def test_a_reference_that_does_not_resolve_degrades(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # A reference that does not resolve is a degraded provider, not an
        # error: the pack still assembles.
        with pytest.raises(WorkItemNotFoundError, match="404"):
            build(store, [ticket("1")]).collect(focus_on("404"), scope)

    def test_no_reference_means_no_current_item(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        found = by_kind(build(store, [ticket("1")]).collect(focus_on(), scope))
        assert "work.current" not in found

    def test_the_summary_is_still_reported_alongside_it(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        found = by_kind(build(store, [ticket("1"), ticket("2")]).collect(focus_on("1"), scope))
        assert found["work.counted"] == 2
        assert found["work.current"]["id"] == "1"


class TestRefresh:
    def test_a_refresh_reaches_the_provider_rather_than_the_cache(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        asked: list[bool] = []

        class Recording(FakeWorkManagementProvider):
            def get_work_item(self, ref: ExternalId, *, refresh: bool = False) -> WorkItem | None:
                asked.append(refresh)
                return super().get_work_item(ref)

        provider = Recording([ticket("1")])
        build(store, provider=provider).collect(focus_on("1", refresh=True), scope)
        assert asked == [True]

    def test_without_it_the_cache_may_answer(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        asked: list[bool] = []

        class Recording(FakeWorkManagementProvider):
            def get_work_item(self, ref: ExternalId, *, refresh: bool = False) -> WorkItem | None:
                asked.append(refresh)
                return super().get_work_item(ref)

        provider = Recording([ticket("1")])
        build(store, provider=provider).collect(focus_on("1"), scope)
        assert asked == [False]

    def test_a_provider_that_does_not_cache_is_asked_plainly(
        self, store: InMemoryDocumentStore, scope: ResolvedScope
    ) -> None:
        # The port's `get_work_item` takes no `refresh`; only the caching
        # decorator adds one. A provider without it must still be usable.
        found = by_kind(build(store, [ticket("1")]).collect(focus_on("1", refresh=True), scope))
        assert found["work.current"]["id"] == "1"
