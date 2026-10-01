"""GitRepositoryLocator -- repository discovery by walking up for `.git`.

core/07 section 13 says to prefer invoking the installed `git` over parsing
`.git/` internals. Finding the root is the one case where that guidance does not
apply: `git rev-parse --show-toplevel` costs a subprocess to answer a question
that is a loop over parent directories, and it fails on a machine with no `git`
installed at all -- where the repository, and the answer, still exist.

Nothing here parses anything inside `.git/`. Its *presence* is the whole signal,
which is why a worktree or submodule, where `.git` is a file rather than a
directory, is recognised as readily as an ordinary clone.

**Which repository a worktree belongs to is the exception, and asks `git`.**
Answering it means following the `gitdir:` in a `.git` file and then
the `commondir` inside that, which is exactly the parsing of `.git/` internals
core/07 section 13 steers away from. The cost argument above does not carry
over either: :meth:`GitRepositoryLocator.main_worktree` runs `git` only when
`.git` is a file and the registry has already missed, and a machine with no
`git` has no worktrees to ask about.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path, PurePath
from typing import Any, Final

from never4ga.domain.identity import ConceptId
from never4ga.errors import RepositoryMarkerError
from never4ga.ports.repository_locator import MARKER_FILENAME, MARKER_WORKSPACE_KEY

__all__ = ["GitRepositoryLocator"]

_ENCODING: Final = "utf-8"
_GIT: Final = ".git"

#: Resolution sits on the startup path, which core/07 section 15 requires to
#: stay bounded. The same ceiling `GitSignalProvider` uses.
TIMEOUT_SECONDS: Final = 5.0


class GitRepositoryLocator:
    def __init__(self, executable: str = "git", timeout: float = TIMEOUT_SECONDS) -> None:
        self._executable = executable
        self._timeout = timeout

    def repository_root(self, start: PurePath) -> PurePath | None:
        candidate = Path(start)
        for directory in (candidate, *candidate.parents):
            if (directory / _GIT).exists():
                return PurePath(directory)
        return None

    def main_worktree(self, repository_root: PurePath) -> PurePath | None:
        root = Path(repository_root)
        if not (root / _GIT).is_file():
            # An ordinary clone, and the common case: answered without `git`.
            return None
        try:
            completed = subprocess.run(
                [self._executable, "-C", str(root), "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except OSError, subprocess.TimeoutExpired:
            return None
        if completed.returncode != 0:
            return None

        # Relative to `root` when git prints it relative; joining an absolute
        # answer keeps it as it is.
        common = (root / completed.stdout.strip()).resolve()
        if common.name != _GIT:
            # A submodule's common directory is `.git/modules/<name>` in the
            # superproject, and a bare repository's is the repository itself.
            # Neither has a main checkout that could be mapped.
            return None
        main = common.parent
        return PurePath(main) if main != root.resolve() else None

    def visible(self, path: PurePath) -> bool:
        return Path(path).exists()

    def read_marker(self, repository_root: PurePath) -> ConceptId | None:
        marker = Path(repository_root, MARKER_FILENAME)
        try:
            text = marker.read_text(encoding=_ENCODING)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RepositoryMarkerError(f"cannot read the marker at {marker}: {error}") from error

        try:
            document: Any = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise RepositoryMarkerError(
                f"the marker at {marker} is not valid TOML: {error}"
            ) from error

        raw = document.get(MARKER_WORKSPACE_KEY)
        if raw is None:
            raise RepositoryMarkerError(
                f"the marker at {marker} names no workspace; it should hold "
                f'{MARKER_WORKSPACE_KEY} = "<uuid>" and nothing else'
            )
        try:
            return ConceptId.parse(str(raw))
        except ValueError as error:
            raise RepositoryMarkerError(
                f"the marker at {marker} does not name a workspace UUID: {error}"
            ) from error

    def write_marker(self, repository_root: PurePath, workspace_id: ConceptId) -> PurePath:
        """Replace the marker whole.

        The managed-block rule (`details/security-configuration.md` section
        7.1, condition 5) governs a file that holds content Never4gA did not
        author. This file holds one line, all of it Never4gA's, so replacing it
        whole is the correct operation rather than an exception to that rule.
        """
        marker = Path(repository_root, MARKER_FILENAME)
        marker.write_text(f'{MARKER_WORKSPACE_KEY} = "{workspace_id}"\n', encoding=_ENCODING)
        return PurePath(marker)

    def exists(self, repository_root: PurePath, relative: str) -> bool:
        return Path(repository_root, relative).exists()

    def read_text(self, repository_root: PurePath, relative: str) -> str | None:
        try:
            return Path(repository_root, relative).read_text(encoding=_ENCODING)
        except OSError, UnicodeDecodeError:
            # Absent, unreadable, or not text. All three mean the same thing to
            # the one caller: there is nothing here to check.
            return None

    def write_text(self, repository_root: PurePath, relative: str, content: str) -> None:
        target = Path(repository_root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=_ENCODING)
