"""GitSignalProvider against real repositories.

core/07 section 13 says to invoke the installed `git` rather than parse `.git/`,
so these tests use real repositories rather than mocking a subprocess. What is
actually being verified is that the signals are *facts* -- short, structured,
mechanical -- and that a repository Never4gA cannot read degrades rather than
breaks the pack.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePath

import pytest

from never4ga.adapters.git import GitSignalProvider
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--initial-branch", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("# thing\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Add a readme")
    return root


def scope_for(root: PurePath | None) -> ResolvedScope:
    return ResolvedScope(
        workspace_id=ConceptId.new(),
        workspace_path=VaultPath.parse("10_Workspaces/thing/workspace.md"),
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
        repository_root=root,
    )


def request() -> ContextRequest:
    return ContextRequest(
        scope=ScopeRequest(),
        depth=ContextDepth.STARTUP,
        budget=ContextBudget(max_items=10, max_characters=1000),
    )


def signals(root: PurePath | None) -> dict[str, object]:
    collected = GitSignalProvider().collect(request(), scope_for(root))
    return {signal.kind: signal.value for signal in collected}


class TestCleanRepository:
    def test_the_branch_is_reported(self, repository: Path) -> None:
        assert signals(repository)["git.branch"] == "main"

    def test_the_head_is_reported_short(self, repository: Path) -> None:
        head = signals(repository)["git.head"]
        assert isinstance(head, str) and 0 < len(head) <= 12

    def test_a_clean_tree_is_not_dirty(self, repository: Path) -> None:
        assert signals(repository)["git.dirty"] is False

    def test_recent_commit_subjects_are_reported(self, repository: Path) -> None:
        assert signals(repository)["git.recent_commits"] == ["Add a readme"]

    def test_a_clean_tree_reports_no_changed_paths(self, repository: Path) -> None:
        assert "git.changed_paths" not in signals(repository)


class TestDirtyRepository:
    def test_a_modified_file_is_reported(self, repository: Path) -> None:
        (repository / "README.md").write_text("# changed\n")
        collected = signals(repository)
        assert collected["git.dirty"] is True
        assert collected["git.changed_paths"] == ["README.md"]

    def test_an_untracked_file_is_reported(self, repository: Path) -> None:
        (repository / "new.md").write_text("new\n")
        assert signals(repository)["git.changed_paths"] == ["new.md"]

    def test_the_changed_count_is_reported(self, repository: Path) -> None:
        for name in ("a.md", "b.md", "c.md"):
            (repository / name).write_text("x\n")
        assert signals(repository)["git.changed_count"] == 3


class TestSignalsAreFacts:
    def test_every_signal_is_mechanical(self, repository: Path) -> None:
        collected = GitSignalProvider().collect(request(), scope_for(repository))
        assert collected
        assert all(signal.reason.is_mechanical for signal in collected)

    def test_every_signal_names_this_provider(self, repository: Path) -> None:
        collected = GitSignalProvider().collect(request(), scope_for(repository))
        assert {signal.provider_id for signal in collected} == {"git"}

    def test_a_very_long_commit_subject_is_clipped_rather_than_rejected(
        self, repository: Path
    ) -> None:
        # ContextSignal refuses anything over 120 characters, so a real
        # repository with a long subject line must not be able to break startup.
        (repository / "x.md").write_text("x\n")
        git(repository, "add", ".")
        git(repository, "commit", "-m", "s" * 400)
        subjects = signals(repository)["git.recent_commits"]
        assert isinstance(subjects, list)
        assert all(len(subject) <= 120 for subject in subjects)


class TestNothingToSay:
    def test_a_workspace_without_a_repository_yields_no_signals(self) -> None:
        # Not a degradation: a Life area has no repository and never will.
        assert GitSignalProvider().collect(request(), scope_for(None)) == ()

    def test_a_directory_that_is_not_a_repository_raises(self, tmp_path: Path) -> None:
        # Raising is right: the assembler turns it into a degraded provider, and
        # a scope that named a repository root which is not one is a real fault.
        with pytest.raises(subprocess.CalledProcessError):
            GitSignalProvider().collect(request(), scope_for(tmp_path))

    def test_a_missing_git_binary_raises(self, repository: Path) -> None:
        provider = GitSignalProvider(executable="definitely-not-git")
        with pytest.raises(OSError):
            provider.collect(request(), scope_for(repository))


class TestDegradationInAPack:
    def test_a_broken_provider_leaves_the_pack_standing(self, tmp_path: Path) -> None:
        from never4ga.adapters.fakes import InMemoryMetadataIndex
        from never4ga.context.assembler import StartupContextAssembler
        from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
        from never4ga.domain.scope import WorkspaceMapping
        from never4ga.ports.metadata_index import MetadataRecord

        workspace = ConceptId.new()
        registry = WorkspaceRegistry()
        registry.register(
            WorkspaceMapping(
                workspace_id=workspace,
                workspace_path=VaultPath.parse("10_Workspaces/thing/workspace.md"),
                repository_root=PurePath(tmp_path),
            )
        )
        index = InMemoryMetadataIndex()
        index.upsert(
            MetadataRecord(
                concept_id=workspace,
                concept_type="workspace",
                path=VaultPath.parse("10_Workspaces/thing/workspace.md"),
                title="Thing",
            )
        )
        assembler = StartupContextAssembler(
            resolver=MechanicalScopeResolver(registry),
            metadata_index=index,
            signal_providers=(GitSignalProvider(),),
        )
        pack = assembler.assemble(
            ContextRequest(
                scope=ScopeRequest(workspace_id=workspace),
                depth=ContextDepth.STARTUP,
                budget=ContextBudget(max_items=10, max_characters=1000),
            )
        )
        assert pack.degraded_providers == ("git",)
        assert pack.items


class TestARepositoryWithNoCommits:
    """A freshly initialised repository is an ordinary place to be working."""

    @pytest.fixture
    def empty(self, tmp_path: Path) -> Path:
        root = tmp_path / "fresh"
        root.mkdir()
        git(root, "init", "--initial-branch", "main")
        return root

    def test_the_branch_is_still_reported(self, empty: Path) -> None:
        assert signals(empty)["git.branch"] == "main"

    def test_there_is_no_head_to_report(self, empty: Path) -> None:
        assert "git.head" not in signals(empty)

    def test_there_are_no_commits_to_report(self, empty: Path) -> None:
        assert "git.recent_commits" not in signals(empty)

    def test_an_untracked_file_is_still_reported(self, empty: Path) -> None:
        (empty / "first.md").write_text("first\n")
        assert signals(empty)["git.changed_paths"] == ["first.md"]

    def test_the_provider_does_not_degrade(self, empty: Path) -> None:
        # The whole point: an empty repository must not cost the pack its
        # Git signals entirely.
        assert GitSignalProvider().collect(request(), scope_for(empty))
