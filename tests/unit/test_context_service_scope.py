"""A pack describes where the session is, not where the registry points.

`ContextService` hands the assembler the scope it resolved, not just the
workspace id. If the assembler re-resolved from the registry, every signal
provider would read the mapped repository root, which inside a worktree is the
main checkout: a pack built in `task-1` would report `git.branch = main`.

A worktree resolves to its repository's workspace and reports itself as the
repository root. The pack follows that answer instead of asking the registry a
second question.
"""

from __future__ import annotations

from pathlib import PurePath

import pytest

from never4ga.adapters.fakes import (
    FakeRepositoryLocator,
    InMemoryDocumentStore,
    InMemoryGraphIndex,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
    InMemoryWorkspaceMappingStore,
)
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ResolvedScope, ScopeRequest
from never4ga.services.context import ContextService
from never4ga.services.workspaces import WorkspaceService

WORKSPACE = ConceptId.new()
MAIN_CHECKOUT = PurePath("/Projects/dev-environment")
WORKTREE = PurePath("/cache/worktrees/task-1")
UNMAPPED_CLONE = PurePath("/Projects/dev-environment-2")


class Recording:
    """A signal provider that keeps the scope the assembler resolved."""

    provider_id = "recording"

    def __init__(self) -> None:
        self.seen: list[ResolvedScope] = []

    def supports(self, request: ContextRequest) -> bool:
        return True

    def collect(self, request: ContextRequest, scope: ResolvedScope) -> tuple[()]:
        self.seen.append(scope)
        return ()


@pytest.fixture
def recording() -> Recording:
    return Recording()


@pytest.fixture
def locator() -> FakeRepositoryLocator:
    return FakeRepositoryLocator(roots=(UNMAPPED_CLONE,), worktrees={WORKTREE: MAIN_CHECKOUT})


@pytest.fixture
def service(recording: Recording, locator: FakeRepositoryLocator) -> ContextService:
    workspaces = WorkspaceService(InMemoryWorkspaceMappingStore(), locator)
    workspaces.map(
        workspace_id=WORKSPACE,
        workspace_path=VaultPath.parse("10_Workspaces/Example/workspace.md"),
        repository_root=MAIN_CHECKOUT,
    )
    return ContextService(
        workspaces=workspaces,
        metadata=InMemoryMetadataIndex(),
        text=InMemoryTextIndex(),
        graph=InMemoryGraphIndex(),
        documents=InMemoryDocumentStore(),
        signal_providers=(recording,),
    )


def _request(path: PurePath, depth: ContextDepth = ContextDepth.STARTUP) -> ContextRequest:
    return ContextRequest(
        scope=ScopeRequest(cwd=path),
        depth=depth,
        budget=ContextBudget(max_items=5, max_characters=5_000),
    )


class TestTheScopeSignalsRead:
    @pytest.mark.parametrize(
        "depth", [ContextDepth.STARTUP, ContextDepth.FOCUSED, ContextDepth.DEEP]
    )
    def test_a_provider_is_given_the_repository_the_session_asked_from(
        self, service: ContextService, recording: Recording, depth: ContextDepth
    ) -> None:
        service.assemble(_request(WORKTREE, depth=depth))
        assert recording.seen[0].repository_root == WORKTREE

    def test_the_pack_reports_that_repository_too(
        self, service: ContextService, recording: Recording
    ) -> None:
        pack, _ = service.assemble(_request(WORKTREE))
        assert pack.scope.repository_root == WORKTREE

    def test_why_it_resolved_that_way_survives_into_the_pack(self, service: ContextService) -> None:
        pack, resolution = service.assemble(_request(WORKTREE))
        assert pack.scope.reason.detail == "worktree"
        assert pack.scope == resolution.scope

    def test_an_ordinary_checkout_is_unchanged(
        self, service: ContextService, recording: Recording
    ) -> None:
        pack, _ = service.assemble(_request(MAIN_CHECKOUT / "src"))
        assert recording.seen[0].repository_root == MAIN_CHECKOUT
        assert pack.scope.workspace_id == WORKSPACE

    def test_a_clone_reached_by_its_marker_is_the_repository_too(
        self, service: ContextService, recording: Recording, locator: FakeRepositoryLocator
    ) -> None:
        """The other place resolution outruns the registry.

        A repository cloned onto a machine that never mapped it resolves by its
        marker, which names a workspace and not a location -- so the registry's
        answer is some *other* clone's path. The session is in this one.
        """
        locator.write_marker(UNMAPPED_CLONE, WORKSPACE)
        pack, _ = service.assemble(_request(UNMAPPED_CLONE))
        assert recording.seen[0].repository_root == UNMAPPED_CLONE
        assert pack.scope.reason.detail == "repository_marker"

    def test_an_explicit_workspace_id_still_resolves_through_the_registry(
        self, service: ContextService, recording: Recording
    ) -> None:
        # Nothing said where the session is, so the mapped root is the only
        # answer there is -- and it is the right one.
        service.assemble(
            ContextRequest(
                scope=ScopeRequest(workspace_id=WORKSPACE),
                depth=ContextDepth.STARTUP,
                budget=ContextBudget(max_items=5, max_characters=5_000),
            )
        )
        assert recording.seen[0].repository_root == MAIN_CHECKOUT
