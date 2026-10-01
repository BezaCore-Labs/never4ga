"""Workspace resolution against machine-local state.

A cwd inside a repository resolves its workspace, a nested path resolves, and
an unrelated repository does not guess.

`MechanicalScopeResolver` decides precedence between deterministic inputs. This
service adds everything that requires knowing about *this machine*: discovering
which repository a directory is in, reading a repository's marker, and the rule
that the registry outranks the marker.
"""

from __future__ import annotations

from pathlib import PurePath

import pytest

from never4ga.adapters.fakes import (
    FakeRepositoryLocator,
    InMemoryDocumentStore,
    InMemoryWorkspaceMappingStore,
)
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.errors import PathNotVisibleError, RepositoryMarkerError, ScopeResolutionError
from never4ga.services.workspaces import WorkspaceService

PARENT = ConceptId.new()
CHILD = ConceptId.new()
UNMAPPED = ConceptId.new()

BEZACORE = PurePath("/home/user/Projects/bezacore")
NEVER4GA = PurePath("/home/user/Projects/bezacore/never4ga")
ELSEWHERE = PurePath("/home/user/Projects/someone-elses-repo")


@pytest.fixture
def locator() -> FakeRepositoryLocator:
    return FakeRepositoryLocator(roots=[BEZACORE, NEVER4GA, ELSEWHERE])


@pytest.fixture
def store() -> InMemoryWorkspaceMappingStore:
    return InMemoryWorkspaceMappingStore(
        [
            WorkspaceMapping(
                workspace_id=PARENT,
                workspace_path=VaultPath.parse("10_Workspaces/BezaCore-Labs/workspace.md"),
                repository_root=BEZACORE,
            ),
            WorkspaceMapping(
                workspace_id=CHILD,
                workspace_path=VaultPath.parse(
                    "10_Workspaces/BezaCore-Labs/Workspaces/never4ga/workspace.md"
                ),
                repository_root=NEVER4GA,
                parent_id=PARENT,
            ),
        ]
    )


@pytest.fixture
def service(
    store: InMemoryWorkspaceMappingStore, locator: FakeRepositoryLocator
) -> WorkspaceService:
    return WorkspaceService(store, locator)


class TestResolvingFromAWorkingDirectory:
    def test_a_cwd_inside_a_mapped_repo_resolves_its_workspace(
        self, service: WorkspaceService
    ) -> None:
        resolution = service.resolve(ScopeRequest(cwd=NEVER4GA))
        assert resolution.scope.workspace_id == CHILD

    def test_a_nested_path_resolves(self, service: WorkspaceService) -> None:
        resolution = service.resolve(ScopeRequest(cwd=NEVER4GA / "src" / "never4ga" / "ports"))
        assert resolution.scope.workspace_id == CHILD

    def test_a_repo_nested_inside_another_resolves_to_itself(
        self, service: WorkspaceService
    ) -> None:
        # The most specific mapping wins: the inner repository is its own
        # workspace.
        assert service.resolve(ScopeRequest(cwd=BEZACORE / "docs")).scope.workspace_id == PARENT
        assert service.resolve(ScopeRequest(cwd=NEVER4GA / "src")).scope.workspace_id == CHILD

    def test_the_parent_chain_is_reported(self, service: WorkspaceService) -> None:
        assert service.resolve(ScopeRequest(cwd=NEVER4GA)).scope.parent_chain == (PARENT,)

    def test_an_unrelated_repo_does_not_guess(self, service: WorkspaceService) -> None:
        with pytest.raises(ScopeResolutionError):
            service.resolve(ScopeRequest(cwd=ELSEWHERE))

    def test_a_directory_in_no_repository_does_not_guess(self, service: WorkspaceService) -> None:
        with pytest.raises(ScopeResolutionError):
            service.resolve(ScopeRequest(cwd=PurePath("/tmp/nothing-here")))

    def test_resolution_reads_the_store_each_time(
        self, service: WorkspaceService, store: InMemoryWorkspaceMappingStore
    ) -> None:
        # A mapping added by another process must be visible without a restart.
        store.save([])
        with pytest.raises(ScopeResolutionError):
            service.resolve(ScopeRequest(cwd=NEVER4GA))

    def test_resolution_is_mechanical(self, service: WorkspaceService) -> None:
        assert service.resolve(ScopeRequest(cwd=NEVER4GA)).scope.reason.is_mechanical


WORKTREE = PurePath("/home/user/.cache/orca/worktrees/never4ga-task-1")


class TestAWorktree:
    """A linked worktree resolves through the repository it belongs to.

    Worktrees are made per task and thrown away, so mapping each one by hand
    cannot keep up; the repository they were made from is the one that is
    mapped.
    """

    @pytest.fixture
    def locator(self) -> FakeRepositoryLocator:
        return FakeRepositoryLocator(
            roots=[BEZACORE, NEVER4GA, ELSEWHERE], worktrees={WORKTREE: NEVER4GA}
        )

    def test_a_worktree_of_a_mapped_repo_resolves_its_workspace(
        self, service: WorkspaceService
    ) -> None:
        assert service.resolve(ScopeRequest(cwd=WORKTREE)).scope.workspace_id == CHILD

    def test_a_path_inside_the_worktree_resolves(self, service: WorkspaceService) -> None:
        resolution = service.resolve(ScopeRequest(cwd=WORKTREE / "src" / "never4ga"))
        assert resolution.scope.workspace_id == CHILD

    def test_the_parent_chain_comes_with_it(self, service: WorkspaceService) -> None:
        assert service.resolve(ScopeRequest(cwd=WORKTREE)).scope.parent_chain == (PARENT,)

    def test_the_scope_names_the_worktree_rather_than_the_main_checkout(
        self, service: WorkspaceService
    ) -> None:
        # Git signals read the scope's repository root. The branch an agent is
        # working on is the worktree's, not whatever the main checkout has.
        assert service.resolve(ScopeRequest(cwd=WORKTREE)).scope.repository_root == WORKTREE

    def test_the_reason_says_it_came_through_a_worktree(self, service: WorkspaceService) -> None:
        reason = service.resolve(ScopeRequest(cwd=WORKTREE)).scope.reason
        assert reason.detail == "worktree"
        assert reason.is_mechanical

    def test_an_explicit_repository_root_that_is_a_worktree_resolves(
        self, service: WorkspaceService
    ) -> None:
        resolution = service.resolve(ScopeRequest(repository_root=WORKTREE))
        assert resolution.scope.workspace_id == CHILD

    def test_a_worktree_mapped_in_its_own_right_keeps_its_own_mapping(
        self, service: WorkspaceService
    ) -> None:
        # The registry is the most specific statement there is; the fallback
        # only runs when it has nothing to say.
        service.map(
            workspace_id=UNMAPPED,
            workspace_path=VaultPath.parse("10_Workspaces/Elsewhere/workspace.md"),
            repository_root=WORKTREE,
        )
        assert service.resolve(ScopeRequest(cwd=WORKTREE)).scope.workspace_id == UNMAPPED

    def test_a_worktree_of_an_unmapped_repo_still_does_not_guess(
        self, store: InMemoryWorkspaceMappingStore
    ) -> None:
        stray = PurePath("/tmp/worktrees/someone-else-task")
        locator = FakeRepositoryLocator(roots=[ELSEWHERE], worktrees={stray: ELSEWHERE})
        with pytest.raises(ScopeResolutionError, match=str(stray)):
            WorkspaceService(store, locator).resolve(ScopeRequest(cwd=stray))

    def test_a_main_checkout_s_marker_conflict_is_still_reported(
        self, service: WorkspaceService, locator: FakeRepositoryLocator
    ) -> None:
        locator.put_raw_marker(NEVER4GA, f'workspace = "{PARENT}"\n')
        resolution = service.resolve(ScopeRequest(cwd=WORKTREE))
        assert resolution.scope.workspace_id == CHILD
        assert resolution.conflicts


HIDDEN = PurePath("/tmp/scratch/never4ga/worktrees/task-1")


class TestAPathThisProcessCannotSee:
    """A sandboxed service cannot see every path its caller can.

    systemd's `PrivateTmp=true` gives the service its own `/tmp`, so a worktree
    under the caller's `/tmp` is not there at all as the service sees it.
    Reporting that as "not mapped to a workspace; register the repository"
    would be advice that cannot work.
    """

    @pytest.fixture
    def locator(self) -> FakeRepositoryLocator:
        return FakeRepositoryLocator(roots=[BEZACORE, NEVER4GA, ELSEWHERE], hidden=[HIDDEN])

    def test_a_cwd_that_cannot_be_seen_says_so(self, service: WorkspaceService) -> None:
        with pytest.raises(PathNotVisibleError, match=str(HIDDEN)):
            service.resolve(ScopeRequest(cwd=HIDDEN / "src"))

    def test_an_explicit_root_that_cannot_be_seen_says_so(self, service: WorkspaceService) -> None:
        with pytest.raises(PathNotVisibleError, match=str(HIDDEN)):
            service.resolve(ScopeRequest(repository_root=HIDDEN))

    def test_the_refusal_does_not_blame_a_mapping(self, service: WorkspaceService) -> None:
        with pytest.raises(PathNotVisibleError) as caught:
            service.resolve(ScopeRequest(cwd=HIDDEN))
        assert "register" not in str(caught.value)
        assert "not mapped" not in str(caught.value)

    def test_it_is_still_a_scope_that_did_not_resolve(self, service: WorkspaceService) -> None:
        # Every caller that catches the broader refusal keeps catching it.
        with pytest.raises(ScopeResolutionError):
            service.resolve(ScopeRequest(cwd=HIDDEN))

    def test_a_mapped_root_resolves_whether_or_not_it_can_be_seen(
        self, store: InMemoryWorkspaceMappingStore
    ) -> None:
        # The registry answers from the path alone. Only a resolution that has
        # already failed asks whether the path was there to be looked at.
        locator = FakeRepositoryLocator(roots=[NEVER4GA], hidden=[NEVER4GA])
        service = WorkspaceService(store, locator)
        assert service.resolve(ScopeRequest(repository_root=NEVER4GA)).scope.workspace_id == CHILD

    def test_a_visible_unmapped_directory_is_still_unmapped(
        self, service: WorkspaceService
    ) -> None:
        with pytest.raises(ScopeResolutionError) as caught:
            service.resolve(ScopeRequest(cwd=ELSEWHERE))
        assert not isinstance(caught.value, PathNotVisibleError)


class TestTheMarker:
    def test_a_marker_resolves_a_repo_the_registry_does_not_know(
        self, service: WorkspaceService, locator: FakeRepositoryLocator
    ) -> None:
        # The marker's real value: a repository this machine has not mapped,
        # naming a workspace it has.
        locator.write_marker(ELSEWHERE, CHILD)
        resolution = service.resolve(ScopeRequest(cwd=ELSEWHERE / "src"))
        assert resolution.scope.workspace_id == CHILD

    def test_the_registry_outranks_a_disagreeing_marker(
        self, service: WorkspaceService, locator: FakeRepositoryLocator
    ) -> None:
        # Authority ranks before specificity: a marker may have been committed
        # by a colleague, while the registry is this user's own machine.
        locator.write_marker(NEVER4GA, PARENT)
        assert service.resolve(ScopeRequest(cwd=NEVER4GA)).scope.workspace_id == CHILD

    def test_a_disagreement_is_reported_rather_than_silently_resolved(
        self, service: WorkspaceService, locator: FakeRepositoryLocator
    ) -> None:
        locator.write_marker(NEVER4GA, PARENT)
        resolution = service.resolve(ScopeRequest(cwd=NEVER4GA))
        assert resolution.conflicts
        assert str(PARENT) in resolution.conflicts[0]

    def test_an_agreeing_marker_is_not_a_conflict(
        self, service: WorkspaceService, locator: FakeRepositoryLocator
    ) -> None:
        locator.write_marker(NEVER4GA, CHILD)
        assert service.resolve(ScopeRequest(cwd=NEVER4GA)).conflicts == ()

    def test_no_marker_is_not_a_conflict(self, service: WorkspaceService) -> None:
        assert service.resolve(ScopeRequest(cwd=NEVER4GA)).conflicts == ()

    def test_a_marker_naming_an_unregistered_workspace_says_so(
        self, service: WorkspaceService, locator: FakeRepositoryLocator
    ) -> None:
        locator.write_marker(ELSEWHERE, UNMAPPED)
        with pytest.raises(ScopeResolutionError, match="not registered"):
            service.resolve(ScopeRequest(cwd=ELSEWHERE))

    def test_a_damaged_marker_is_not_swallowed(
        self, service: WorkspaceService, locator: FakeRepositoryLocator
    ) -> None:
        locator.put_raw_marker(ELSEWHERE, "workspace = 'nonsense'")
        with pytest.raises(RepositoryMarkerError):
            service.resolve(ScopeRequest(cwd=ELSEWHERE))


class TestExplicitInputs:
    def test_an_explicit_workspace_id_needs_no_repository(self, service: WorkspaceService) -> None:
        assert service.resolve(ScopeRequest(workspace_id=PARENT)).scope.workspace_id == PARENT

    def test_an_explicit_repository_root_skips_discovery(self, service: WorkspaceService) -> None:
        resolution = service.resolve(
            ScopeRequest(cwd=PurePath("/somewhere/else"), repository_root=BEZACORE)
        )
        assert resolution.scope.workspace_id == PARENT

    def test_an_empty_request_cannot_resolve(self, service: WorkspaceService) -> None:
        with pytest.raises(ScopeResolutionError):
            service.resolve(ScopeRequest())


class TestMappingRepositories:
    def test_mapping_a_repository_makes_it_resolvable(self, service: WorkspaceService) -> None:
        service.map(
            workspace_id=UNMAPPED,
            workspace_path=VaultPath.parse("10_Workspaces/other/workspace.md"),
            repository_root=ELSEWHERE,
        )
        assert service.resolve(ScopeRequest(cwd=ELSEWHERE / "src")).scope.workspace_id == UNMAPPED

    def test_mapping_the_same_repository_twice_replaces_rather_than_duplicates(
        self, service: WorkspaceService
    ) -> None:
        for _ in range(2):
            service.map(
                workspace_id=UNMAPPED,
                workspace_path=VaultPath.parse("10_Workspaces/other/workspace.md"),
                repository_root=ELSEWHERE,
            )
        assert len([m for m in service.mappings() if m.workspace_id == UNMAPPED]) == 1

    def test_a_workspace_may_own_several_repositories(self, service: WorkspaceService) -> None:
        # core/03's `repositories` is plural: one workspace may own several
        # repositories. Mapping a second root must not forget the first.
        second = PurePath("/home/user/dotfiles")
        for root in (ELSEWHERE, second):
            service.map(
                workspace_id=UNMAPPED,
                workspace_path=VaultPath.parse("10_Workspaces/other/workspace.md"),
                repository_root=root,
            )
        assert len([m for m in service.mappings() if m.workspace_id == UNMAPPED]) == 2
        for root in (ELSEWHERE, second):
            assert service.resolve(ScopeRequest(cwd=root / "src")).scope.workspace_id == UNMAPPED

    def test_mapping_a_repository_to_another_workspace_moves_it(
        self, service: WorkspaceService
    ) -> None:
        # A repository belongs to one workspace at a time; re-mapping it is a
        # correction, not a second claim.
        for workspace_id in (UNMAPPED, CHILD):
            service.map(
                workspace_id=workspace_id,
                workspace_path=VaultPath.parse("10_Workspaces/other/workspace.md"),
                repository_root=ELSEWHERE,
            )
        assert service.resolve(ScopeRequest(cwd=ELSEWHERE / "src")).scope.workspace_id == CHILD
        assert not [
            m
            for m in service.mappings()
            if m.workspace_id == UNMAPPED and m.repository_root == ELSEWHERE
        ]

    def test_unmapping_forgets_every_repository_of_the_workspace(
        self, service: WorkspaceService
    ) -> None:
        for root in (ELSEWHERE, PurePath("/home/user/dotfiles")):
            service.map(
                workspace_id=UNMAPPED,
                workspace_path=VaultPath.parse("10_Workspaces/other/workspace.md"),
                repository_root=root,
            )
        assert service.unmap(UNMAPPED) is True
        assert not [m for m in service.mappings() if m.workspace_id == UNMAPPED]

    def test_mapping_persists_through_the_store(
        self, service: WorkspaceService, store: InMemoryWorkspaceMappingStore
    ) -> None:
        service.map(
            workspace_id=UNMAPPED,
            workspace_path=VaultPath.parse("10_Workspaces/other/workspace.md"),
            repository_root=ELSEWHERE,
        )
        assert UNMAPPED in {m.workspace_id for m in store.load()}

    def test_unmapping_removes_it(self, service: WorkspaceService) -> None:
        assert service.unmap(PARENT) is True
        assert [m.workspace_id for m in service.mappings()] == [CHILD]

    def test_unmapping_a_nested_repo_falls_back_to_the_repo_around_it(
        self, service: WorkspaceService
    ) -> None:
        # Not a guess: the directory is genuinely inside the outer repository,
        # which is mapped. A vendored repository inside a mapped project belongs to that
        # project until it is mapped to something of its own.
        service.unmap(CHILD)
        assert service.resolve(ScopeRequest(cwd=NEVER4GA / "src")).scope.workspace_id == PARENT

    def test_unmapping_everything_leaves_nothing_to_resolve(
        self, service: WorkspaceService
    ) -> None:
        service.unmap(CHILD)
        service.unmap(PARENT)
        with pytest.raises(ScopeResolutionError):
            service.resolve(ScopeRequest(cwd=NEVER4GA))

    def test_unmapping_something_unmapped_reports_that(self, service: WorkspaceService) -> None:
        assert service.unmap(UNMAPPED) is False

    def test_mappings_are_listed_in_a_stable_order(self, service: WorkspaceService) -> None:
        assert service.mappings() == service.mappings()


class TestAMappingThisVaultDoesNotHold:
    """A mapping resolves only to a workspace the vault in hand has.

    Mappings are kept per vault, and this is a second check. A mapping that
    names a workspace this vault lacks, or one that has since moved, must not
    yield an empty pack and a session for a workspace that does not exist.
    """

    @staticmethod
    def _workspace(workspace_id: ConceptId, path: str) -> StoredDocument:
        return StoredDocument(
            concept_id=workspace_id,
            path=VaultPath.parse(path),
            frontmatter={"type": "workspace", "id": str(workspace_id), "title": "W"},
            body="",
        )

    @pytest.fixture
    def documents(self) -> InMemoryDocumentStore:
        documents = InMemoryDocumentStore()
        documents.put(self._workspace(PARENT, "10_Workspaces/BezaCore-Labs/workspace.md"))
        return documents

    @pytest.fixture
    def guarded(
        self,
        store: InMemoryWorkspaceMappingStore,
        locator: FakeRepositoryLocator,
        documents: InMemoryDocumentStore,
    ) -> WorkspaceService:
        return WorkspaceService(store, locator, documents=documents)

    def test_a_workspace_the_vault_holds_resolves(self, guarded: WorkspaceService) -> None:
        assert guarded.resolve(ScopeRequest(cwd=BEZACORE)).scope.workspace_id == PARENT

    def test_a_workspace_the_vault_lacks_is_refused(self, guarded: WorkspaceService) -> None:
        with pytest.raises(ScopeResolutionError, match="does not hold"):
            guarded.resolve(ScopeRequest(cwd=NEVER4GA))

    def test_an_explicit_id_the_vault_lacks_is_refused(self, guarded: WorkspaceService) -> None:
        with pytest.raises(ScopeResolutionError, match="does not hold"):
            guarded.resolve(ScopeRequest(workspace_id=CHILD))

    def test_a_different_concept_at_the_mapped_path_is_refused(
        self,
        store: InMemoryWorkspaceMappingStore,
        locator: FakeRepositoryLocator,
    ) -> None:
        # The workspace moved and something else took its place: identity, not
        # location, is what the mapping names (core/02 section 5.1).
        documents = InMemoryDocumentStore()
        documents.put(self._workspace(UNMAPPED, "10_Workspaces/BezaCore-Labs/workspace.md"))
        service = WorkspaceService(store, locator, documents=documents)
        with pytest.raises(ScopeResolutionError, match="does not hold"):
            service.resolve(ScopeRequest(cwd=BEZACORE))

    def test_the_refusal_names_the_workspace_and_the_way_out(
        self, guarded: WorkspaceService
    ) -> None:
        with pytest.raises(ScopeResolutionError) as caught:
            guarded.resolve(ScopeRequest(cwd=NEVER4GA))
        message = str(caught.value)
        assert str(CHILD) in message
        assert "workspace map" in message
