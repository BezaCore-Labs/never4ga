"""A startup pack says whether the tracker can be written to, not just what is in it.

`sync_policy` (`core/03`) is the first of two gates: the workspace decides
whether writing is possible at all, and `--apply` decides whether one particular
write happens. `work update` against a `read` workspace refuses with the right
hint.

That refusal comes at the point of use, which is the wrong moment for the only
party that could act. An agent reading a list of open tickets needs to know
whether it may touch them.

So the policy is part of the pack. `work.writable` answers the question an agent
actually has, and `work.sync_policy` says which declaration produced the answer
-- "you may not" and "you may not *because the workspace says read*" send a
reader to different places.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

import pytest

from never4ga.adapters.fakes import InMemoryDocumentStore, InMemoryTrackerCache
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId, ExternalId, WorkItemKey
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope
from never4ga.domain.work_policy import DEFAULT_POLICY, WRITING_POLICIES
from never4ga.ports.tracker_cache import CachedWorkItem
from tests.unit.test_work_signals import (
    CONNECTION,
    NOW,
    PROJECT,
    WORKSPACE,
    build,
    by_kind,
    integration_document,
    request,
    ticket,
    workspace_document,
)


@pytest.fixture
def workspace_id() -> ConceptId:
    return ConceptId.new()


@pytest.fixture
def scope(workspace_id: ConceptId) -> ResolvedScope:
    return ResolvedScope(
        workspace_id=workspace_id,
        workspace_path=VaultPath.parse(WORKSPACE),
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
        repository_root=PurePath("/home/user/Projects/never4ga"),
    )


def signals(workspace_id: ConceptId, scope: ResolvedScope, **declaration: Any) -> dict[str, Any]:
    documents = InMemoryDocumentStore()
    documents.put(
        workspace_document(workspace_id, mode="external", connection=CONNECTION, **declaration)
    )
    documents.put(integration_document())
    return by_kind(build(documents, [ticket("1")]).collect(request(), scope))


class TestThePolicyIsPartOfThePack:
    def test_read_write_is_reported_as_writable(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        emitted = signals(workspace_id, scope, sync_policy="read_write")

        assert emitted["work.writable"] is True
        assert emitted["work.sync_policy"] == "read_write"

    def test_read_is_reported_as_not_writable(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        emitted = signals(workspace_id, scope, sync_policy="read")

        assert emitted["work.writable"] is False
        assert emitted["work.sync_policy"] == "read"

    def test_an_undeclared_policy_reports_the_default_it_fell_back_to(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        """Failing closed is half of it; the reader has to know that it happened."""
        emitted = signals(workspace_id, scope)

        assert emitted["work.writable"] is False
        assert emitted["work.sync_policy"] == DEFAULT_POLICY

    def test_the_signal_agrees_with_the_write_gate(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        """One vocabulary, read from `domain/work_policy`.

        A pack that promised what the write path refuses would be worse than a
        pack that stayed quiet, so this asserts against the same constant the
        gate itself uses rather than against a repeated literal.
        """
        for policy in ("read", "read_write"):
            emitted = signals(workspace_id, scope, sync_policy=policy)
            assert emitted["work.writable"] == (policy in WRITING_POLICIES)


class TestItSurvivesAnUnreachableTracker:
    def test_a_cached_pack_still_reports_the_policy(
        self, workspace_id: ConceptId, scope: ResolvedScope
    ) -> None:
        """The declaration is in the vault, so it stays knowable with the tracker down."""
        documents = InMemoryDocumentStore()
        documents.put(
            workspace_document(
                workspace_id, mode="external", connection=CONNECTION, sync_policy="read_write"
            )
        )
        documents.put(integration_document())
        cache = InMemoryTrackerCache()
        cache.put(
            CachedWorkItem(
                key=WorkItemKey(
                    connection=CONNECTION,
                    project_ref=PROJECT,
                    external_id=ExternalId(provider="fake-pm", value="1"),
                ),
                item=ticket("1"),
                fetched_at=NOW,
            )
        )

        emitted = by_kind(build(documents, available=False, cache=cache).collect(request(), scope))

        assert emitted["work.live"] is False
        assert emitted["work.writable"] is True
