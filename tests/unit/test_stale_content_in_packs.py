"""Stale content in a Context Pack (core/02 section 15.2).

Section 15.2 says stale content SHOULD remain available, be visibly flagged,
and be downgraded during context assembly. `search` and `doctor` read
`stale_after`, and so does the pack: otherwise a context document past its own
expiry would reach every session unflagged, in the lane that carries a
workspace's most-read documents.

Flagged: an item carries the instant it went stale. Downgraded: a stale body
yields its room to a fresh one, and keeps its place in the pack as a
reference, so it is never removed -- only outranked for space.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

import pytest

from never4ga.adapters.fakes import InMemoryDocumentStore, InMemoryMetadataIndex
from never4ga.context.assembler import StartupContextAssembler
from never4ga.context.budget import apply_budget
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.context import (
    ContextBudget,
    ContextDepth,
    ContextItem,
    ContextRequest,
    PackCategory,
)
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.metadata_index import MetadataRecord

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
WORKSPACE = ConceptId.new()
REPO = PurePosixPath("/home/user/Projects/never4ga")
CONTEXT_DIR = "10_Workspaces/never4ga/Context"


def item(body: str, priority: int, *, stale_since: datetime | None = None) -> ContextItem:
    return ContextItem(
        concept_id=ConceptId.new(),
        path=VaultPath.parse("10_Workspaces/never4ga/Context/thing.md"),
        title="Thing",
        priority=priority,
        reason=AcquisitionReason.of(ReasonCode.STRUCTURAL_LOCATION),
        body=body,
        stale_since=stale_since,
    )


class TestTheBudgetDowngradesStaleBodies:
    def test_a_stale_body_yields_its_room_to_a_fresh_one(self) -> None:
        stale = item("s" * 60, priority=1, stale_since=NOW)
        fresh = item("f" * 60, priority=2)

        kept, _ = apply_budget([stale, fresh], ContextBudget(max_items=5, max_characters=100))

        by_id = {entry.concept_id: entry for entry in kept}
        assert by_id[fresh.concept_id].body == fresh.body
        # Still present, and still flagged: downgraded, never removed.
        assert by_id[stale.concept_id].is_reference
        assert by_id[stale.concept_id].stale_since == NOW

    def test_the_pack_keeps_its_order(self) -> None:
        stale = item("s" * 10, priority=1, stale_since=NOW)
        fresh = item("f" * 10, priority=2)

        kept, _ = apply_budget([fresh, stale], ContextBudget(max_items=5, max_characters=100))

        assert [entry.concept_id for entry in kept] == [stale.concept_id, fresh.concept_id]

    def test_a_stale_body_is_still_carried_when_there_is_room(self) -> None:
        stale = item("s" * 10, priority=1, stale_since=NOW)
        fresh = item("f" * 10, priority=2)

        kept, usage = apply_budget([stale, fresh], ContextBudget(max_items=5, max_characters=100))

        assert all(entry.body is not None for entry in kept)
        assert usage.full_documents == 2

    def test_the_full_document_limit_is_spent_on_fresh_bodies_first(self) -> None:
        stale = item("s", priority=1, stale_since=NOW)
        fresh = item("f", priority=2)
        budget = ContextBudget(max_items=5, max_characters=100, max_full_documents=1)

        kept, _ = apply_budget([stale, fresh], budget)

        by_id = {entry.concept_id: entry for entry in kept}
        assert by_id[fresh.concept_id].body == "f"
        assert by_id[stale.concept_id].is_reference


def context_document(title: str, **frontmatter: object) -> tuple[MetadataRecord, StoredDocument]:
    concept_id = ConceptId.new()
    path = VaultPath.parse(f"{CONTEXT_DIR}/{title.lower().replace(' ', '-')}.md")
    record = MetadataRecord(
        concept_id=concept_id,
        concept_type="context",
        path=path,
        title=title,
        workspace_id=WORKSPACE,
    )
    document = StoredDocument(
        concept_id=concept_id,
        path=path,
        frontmatter={"type": "context", "title": title, **frontmatter},
        body=f"# {title}\n",
    )
    return record, document


@pytest.fixture
def assembler() -> StartupContextAssembler:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=WORKSPACE,
            workspace_path=VaultPath.parse("10_Workspaces/never4ga/workspace.md"),
            repository_root=REPO,
        )
    )
    index = InMemoryMetadataIndex()
    store = InMemoryDocumentStore()
    index.upsert(
        MetadataRecord(
            concept_id=WORKSPACE,
            concept_type="workspace",
            path=VaultPath.parse("10_Workspaces/never4ga/workspace.md"),
            title="Never4gA",
        )
    )
    documents = (
        context_document("Expired", stale_after="2026-09-01T00:00:00Z"),
        context_document("Expires Now", stale_after="2026-09-27T12:00:00Z"),
        context_document("Not Yet", stale_after="2026-12-01T00:00:00Z"),
        context_document("Never Said"),
        context_document("Unreadable", stale_after="next week"),
    )
    for record, document in documents:
        index.upsert(record)
        store.put(document)
    return StartupContextAssembler(
        resolver=MechanicalScopeResolver(registry),
        metadata_index=index,
        document_store=store,
        now=lambda: NOW,
    )


def stale_by_title(assembler: StartupContextAssembler) -> dict[str, datetime | None]:
    pack = assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO),
            depth=ContextDepth.STARTUP,
            budget=ContextBudget(max_items=20, max_characters=10_000),
        )
    )
    return {
        entry.title: entry.stale_since
        for entry in pack.items
        if entry.category == PackCategory.CONTEXT
    }


class TestTheStartupPackFlagsStaleContext:
    def test_a_document_past_its_stale_after_is_flagged_with_that_instant(
        self, assembler: StartupContextAssembler
    ) -> None:
        assert stale_by_title(assembler)["Expired"] == datetime(2026, 9, 1, tzinfo=UTC)

    def test_the_boundary_instant_is_already_stale(
        self, assembler: StartupContextAssembler
    ) -> None:
        # Section 15: now >= stale_after.
        assert stale_by_title(assembler)["Expires Now"] == NOW

    def test_a_future_instant_is_not_stale(self, assembler: StartupContextAssembler) -> None:
        assert stale_by_title(assembler)["Not Yet"] is None

    def test_absence_asserts_nothing(self, assembler: StartupContextAssembler) -> None:
        assert stale_by_title(assembler)["Never Said"] is None

    def test_an_unreadable_instant_is_not_guessed_at(
        self, assembler: StartupContextAssembler
    ) -> None:
        assert stale_by_title(assembler)["Unreadable"] is None
