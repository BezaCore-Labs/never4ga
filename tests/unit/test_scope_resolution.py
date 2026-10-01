"""Mechanical scope resolution (core/07 Stage A).

Startup orchestration resolves the workspace from deterministic inputs, with no
LLM.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionStage, ReasonCode
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.errors import ScopeResolutionError

PARENT_ID = ConceptId.new()
CHILD_ID = ConceptId.new()
UNRELATED_ID = ConceptId.new()


@pytest.fixture
def registry() -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=PARENT_ID,
            workspace_path=VaultPath.parse("10_Workspaces/never4ga/workspace.md"),
            repository_root=PurePosixPath("/home/user/Projects/never4ga"),
        )
    )
    registry.register(
        WorkspaceMapping(
            workspace_id=CHILD_ID,
            workspace_path=VaultPath.parse(
                "10_Workspaces/never4ga/Workspaces/runtime/workspace.md"
            ),
            repository_root=PurePosixPath("/home/user/Projects/never4ga/runtime"),
            parent_id=PARENT_ID,
        )
    )
    registry.register(
        WorkspaceMapping(
            workspace_id=UNRELATED_ID,
            workspace_path=VaultPath.parse("10_Workspaces/other/workspace.md"),
            repository_root=PurePosixPath("/home/user/Projects/other"),
        )
    )
    return registry


@pytest.fixture
def resolver(registry: WorkspaceRegistry) -> MechanicalScopeResolver:
    return MechanicalScopeResolver(registry)


class TestScopeResolution:
    def test_explicit_workspace_id_wins(self, resolver: MechanicalScopeResolver) -> None:
        scope = resolver.resolve(ScopeRequest(workspace_id=CHILD_ID))
        assert scope.workspace_id == CHILD_ID
        assert scope.reason.code == ReasonCode.WORKSPACE_REQUIRED

    def test_cwd_inside_a_mapped_repo_resolves_the_workspace(
        self, resolver: MechanicalScopeResolver
    ) -> None:
        scope = resolver.resolve(ScopeRequest(cwd=PurePosixPath("/home/user/Projects/never4ga")))
        assert scope.workspace_id == PARENT_ID

    def test_a_nested_path_resolves_through_the_parent_chain(
        self, resolver: MechanicalScopeResolver
    ) -> None:
        scope = resolver.resolve(
            ScopeRequest(cwd=PurePosixPath("/home/user/Projects/never4ga/src/never4ga/ports"))
        )
        assert scope.workspace_id == PARENT_ID

    def test_the_most_specific_mapping_wins(self, resolver: MechanicalScopeResolver) -> None:
        # The child repo lives inside the parent repo; longest prefix wins.
        scope = resolver.resolve(
            ScopeRequest(cwd=PurePosixPath("/home/user/Projects/never4ga/runtime/src"))
        )
        assert scope.workspace_id == CHILD_ID

    def test_an_unmapped_repo_is_not_guessed(self, resolver: MechanicalScopeResolver) -> None:
        with pytest.raises(ScopeResolutionError):
            resolver.resolve(ScopeRequest(cwd=PurePosixPath("/home/user/Projects/unknown")))

    def test_an_empty_request_cannot_resolve(self, resolver: MechanicalScopeResolver) -> None:
        with pytest.raises(ScopeResolutionError):
            resolver.resolve(ScopeRequest())

    def test_an_unknown_workspace_id_is_an_error(self, resolver: MechanicalScopeResolver) -> None:
        with pytest.raises(ScopeResolutionError):
            resolver.resolve(ScopeRequest(workspace_id=ConceptId.new()))

    def test_explicit_repository_root_is_used_before_cwd(
        self, resolver: MechanicalScopeResolver
    ) -> None:
        scope = resolver.resolve(
            ScopeRequest(
                cwd=PurePosixPath("/somewhere/else"),
                repository_root=PurePosixPath("/home/user/Projects/other"),
            )
        )
        assert scope.workspace_id == UNRELATED_ID

    def test_the_parent_chain_is_reported(self, resolver: MechanicalScopeResolver) -> None:
        scope = resolver.resolve(ScopeRequest(workspace_id=CHILD_ID))
        assert scope.parent_chain == (PARENT_ID,)

    def test_a_root_workspace_has_an_empty_parent_chain(
        self, resolver: MechanicalScopeResolver
    ) -> None:
        assert resolver.resolve(ScopeRequest(workspace_id=PARENT_ID)).parent_chain == ()

    def test_resolution_is_mechanical(self, resolver: MechanicalScopeResolver) -> None:
        scope = resolver.resolve(ScopeRequest(workspace_id=CHILD_ID))
        assert scope.reason.stage is AcquisitionStage.SCOPE
        assert scope.reason.is_mechanical

    def test_resolution_is_deterministic(self, resolver: MechanicalScopeResolver) -> None:
        request = ScopeRequest(cwd=PurePosixPath("/home/user/Projects/never4ga/src"))
        assert resolver.resolve(request) == resolver.resolve(request)

    def test_the_workspace_path_is_reported(self, resolver: MechanicalScopeResolver) -> None:
        scope = resolver.resolve(ScopeRequest(workspace_id=PARENT_ID))
        assert str(scope.workspace_path) == "10_Workspaces/never4ga/workspace.md"


class TestWorkspaceRegistry:
    def test_a_cycle_in_the_parent_chain_is_rejected(self) -> None:
        registry = WorkspaceRegistry()
        first, second = ConceptId.new(), ConceptId.new()
        registry.register(
            WorkspaceMapping(
                workspace_id=first,
                workspace_path=VaultPath.parse("10_Workspaces/a/workspace.md"),
                parent_id=second,
            )
        )
        registry.register(
            WorkspaceMapping(
                workspace_id=second,
                workspace_path=VaultPath.parse("10_Workspaces/b/workspace.md"),
                parent_id=first,
            )
        )
        with pytest.raises(ScopeResolutionError, match="cycle"):
            registry.parent_chain(first)

    def test_a_workspace_registered_with_two_repositories_resolves_from_both(self) -> None:
        registry = WorkspaceRegistry()
        workspace_id = ConceptId.new()
        path = VaultPath.parse("10_Workspaces/dev/workspace.md")
        roots = (
            PurePosixPath("/home/user/Projects/dev-environment"),
            PurePosixPath("/home/user/dotfiles"),
        )
        for root in roots:
            registry.register(
                WorkspaceMapping(
                    workspace_id=workspace_id, workspace_path=path, repository_root=root
                )
            )
        for root in roots:
            resolved = registry.resolve_path(root / "inside")
            assert resolved is not None and resolved.workspace_id == workspace_id
        assert registry.get(workspace_id) is not None

    def test_reregistering_the_same_repository_replaces_its_entry(self) -> None:
        registry = WorkspaceRegistry()
        workspace_id = ConceptId.new()
        root = PurePosixPath("/home/user/Projects/dev-environment")
        for parent in (None, ConceptId.new()):
            registry.register(
                WorkspaceMapping(
                    workspace_id=workspace_id,
                    workspace_path=VaultPath.parse("10_Workspaces/dev/workspace.md"),
                    repository_root=root,
                    parent_id=parent,
                )
            )
        resolved = registry.resolve_path(root)
        assert resolved is not None and resolved.parent_id is not None

    def test_mappings_without_a_repository_are_still_addressable_by_id(self) -> None:
        registry = WorkspaceRegistry()
        workspace_id = ConceptId.new()
        registry.register(
            WorkspaceMapping(
                workspace_id=workspace_id,
                workspace_path=VaultPath.parse("20_Life/health/area.md"),
            )
        )
        assert registry.get(workspace_id) is not None
        assert registry.resolve_path(PurePosixPath("/anywhere")) is None
