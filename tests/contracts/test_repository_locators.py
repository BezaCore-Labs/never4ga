"""Every RepositoryLocator implementation against the one contract."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePath

import pytest

from never4ga.adapters.fakes import FakeRepositoryLocator
from never4ga.adapters.filesystem import GitRepositoryLocator
from never4ga.domain.identity import ConceptId
from never4ga.ports.repository_locator import MARKER_FILENAME, RepositoryLocator
from tests.contracts.repository_locator_contract import RepositoryLocatorContract

pytestmark = pytest.mark.contract


class TestGitRepositoryLocator(RepositoryLocatorContract):
    @pytest.fixture
    def repository(self, tmp_path: Path) -> PurePath:
        root = tmp_path / "Projects" / "never4ga"
        (root / ".git").mkdir(parents=True)
        (root / "src" / "never4ga").mkdir(parents=True)
        return root

    @pytest.fixture
    def outside(self, tmp_path: Path) -> PurePath:
        elsewhere = tmp_path / "not-a-repo"
        elsewhere.mkdir()
        return elsewhere

    @pytest.fixture
    def hidden(self, tmp_path: Path) -> PurePath:
        return tmp_path / "never-made"

    @pytest.fixture
    def locator(self) -> RepositoryLocator:
        return GitRepositoryLocator()

    def put_raw_marker(self, repository: PurePath, text: str) -> None:
        Path(repository, MARKER_FILENAME).write_text(text)

    def put_file(self, repository: PurePath, relative: str, text: str) -> None:
        target = Path(repository, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


class TestFakeRepositoryLocator(RepositoryLocatorContract):
    @pytest.fixture
    def repository(self) -> PurePath:
        return PurePath("/home/user/Projects/never4ga")

    @pytest.fixture
    def outside(self) -> PurePath:
        return PurePath("/home/user/elsewhere")

    @pytest.fixture
    def hidden(self) -> PurePath:
        return PurePath("/tmp/scratch/worktree")

    @pytest.fixture
    def locator(self, repository: PurePath, hidden: PurePath) -> RepositoryLocator:
        self._locator = FakeRepositoryLocator(roots=[repository], hidden=[hidden])
        return self._locator

    def put_raw_marker(self, repository: PurePath, text: str) -> None:
        self._locator.put_raw_marker(repository, text)

    def put_file(self, repository: PurePath, relative: str, text: str) -> None:
        self._locator.put_file(repository, relative, text)


class TestGitDiscoverySpecifics:
    def test_a_git_file_counts_as_a_repository(self, tmp_path: Path) -> None:
        # A worktree or submodule has `.git` as a file, not a directory.
        root = tmp_path / "worktree"
        (root / "src").mkdir(parents=True)
        (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
        assert GitRepositoryLocator().repository_root(root / "src") == root

    def test_the_nearest_repository_wins(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "vendor" / "inner"
        (outer / ".git").mkdir(parents=True)
        (inner / ".git").mkdir(parents=True)
        assert GitRepositoryLocator().repository_root(inner / "src") == inner

    def test_discovery_stops_at_the_filesystem_root(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert GitRepositoryLocator().repository_root(deep) is None

    def test_a_missing_directory_locates_nothing(self, tmp_path: Path) -> None:
        assert GitRepositoryLocator().repository_root(tmp_path / "gone") is None

    def test_the_marker_holds_the_uuid_and_nothing_else(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        workspace = ConceptId.new()
        GitRepositoryLocator().write_marker(root, workspace)
        assert (root / MARKER_FILENAME).read_text() == f'workspace = "{workspace}"\n'


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


def committed_repository(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "--initial-branch", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("# thing\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Add a readme")
    return root


@pytest.fixture
def main(tmp_path: Path) -> Path:
    return committed_repository(tmp_path / "Projects" / "dev-environment")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestGitWorktrees:
    """A linked worktree leads back to the repository it was made from.

    Real repositories rather than hand-written `.git` files, because what is
    being verified is that `git` itself is asked -- core/07 section 13 -- and
    that its answer is read correctly for every shape a `.git` file can take.
    """

    def test_a_linked_worktree_names_its_main_worktree(self, main: Path, tmp_path: Path) -> None:
        worktree = tmp_path / "elsewhere" / "task-1"
        git(main, "worktree", "add", "--detach", str(worktree))
        assert GitRepositoryLocator().main_worktree(worktree) == main

    def test_a_worktree_is_still_its_own_repository_root(self, main: Path, tmp_path: Path) -> None:
        # The fallback is resolution's. Discovery keeps answering with the
        # directory that is checked out, which is what Git signals must read.
        worktree = tmp_path / "task-1"
        git(main, "worktree", "add", "--detach", str(worktree))
        (worktree / "src").mkdir()
        assert GitRepositoryLocator().repository_root(worktree / "src") == worktree

    def test_a_submodule_is_no_worktree(self, main: Path, tmp_path: Path) -> None:
        # A submodule's `.git` is a file too, but it is a repository of its
        # own. Resolving it to the superproject would be a guess.
        library = committed_repository(tmp_path / "library")
        git(main, "-c", "protocol.file.allow=always", "submodule", "add", str(library), "vendor")
        assert (main / "vendor" / ".git").is_file()
        assert GitRepositoryLocator().main_worktree(main / "vendor") is None

    def test_a_worktree_whose_repository_is_gone_names_nothing(self, tmp_path: Path) -> None:
        orphan = tmp_path / "orphan"
        orphan.mkdir()
        (orphan / ".git").write_text(f"gitdir: {tmp_path / 'gone' / '.git' / 'worktrees' / 'x'}\n")
        assert GitRepositoryLocator().main_worktree(orphan) is None

    def test_no_git_installed_names_nothing(self, main: Path, tmp_path: Path) -> None:
        # Degrade rather than fail: core/05 section 19.
        worktree = tmp_path / "task-1"
        git(main, "worktree", "add", "--detach", str(worktree))
        locator = GitRepositoryLocator(executable=str(tmp_path / "no-such-git"))
        assert locator.main_worktree(worktree) is None
