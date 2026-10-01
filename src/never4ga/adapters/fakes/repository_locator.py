"""In-memory RepositoryLocator.

Lets workspace resolution be tested against repositories that never exist on
disk, including shapes awkward to create for real -- a repository nested inside
another, or a marker naming a workspace this machine has never mapped.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import PurePath
from typing import Any

from never4ga.domain.identity import ConceptId
from never4ga.errors import RepositoryMarkerError
from never4ga.ports.repository_locator import MARKER_FILENAME

__all__ = ["FakeRepositoryLocator"]


class FakeRepositoryLocator:
    def __init__(
        self,
        roots: Sequence[PurePath] = (),
        worktrees: Mapping[PurePath, PurePath] | None = None,
        hidden: Sequence[PurePath] = (),
    ) -> None:
        """``worktrees`` maps a linked worktree to its main one.

        A worktree is a repository root of its own, as it is on disk, so each
        one is also a root here without being listed twice.

        ``hidden`` names paths this process cannot see, with everything under
        them. Every other path is visible, declared or not, so a
        directory in no repository is somewhere rather than nowhere.
        """
        self._hidden = tuple(hidden)
        self._worktrees = dict(worktrees or {})
        self._roots = (*roots, *self._worktrees)
        self._markers: dict[PurePath, str] = {}
        self._files: dict[PurePath, dict[str, str]] = {}

    def repository_root(self, start: PurePath) -> PurePath | None:
        containing = [root for root in self._roots if start == root or root in start.parents]
        if not containing:
            return None
        return max(containing, key=lambda root: len(root.parts))

    def main_worktree(self, repository_root: PurePath) -> PurePath | None:
        return self._worktrees.get(repository_root)

    def visible(self, path: PurePath) -> bool:
        return not any(path == hidden or hidden in path.parents for hidden in self._hidden)

    def read_marker(self, repository_root: PurePath) -> ConceptId | None:
        text = self._markers.get(repository_root)
        if text is None:
            return None
        marker = repository_root / MARKER_FILENAME
        try:
            document: Any = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise RepositoryMarkerError(
                f"the marker at {marker} is not valid TOML: {error}"
            ) from error
        raw = document.get("workspace")
        if raw is None:
            raise RepositoryMarkerError(f"the marker at {marker} names no workspace")
        try:
            return ConceptId.parse(str(raw))
        except ValueError as error:
            raise RepositoryMarkerError(
                f"the marker at {marker} does not name a workspace UUID: {error}"
            ) from error

    def write_marker(self, repository_root: PurePath, workspace_id: ConceptId) -> PurePath:
        self._markers[repository_root] = f'workspace = "{workspace_id}"\n'
        return repository_root / MARKER_FILENAME

    def exists(self, repository_root: PurePath, relative: str) -> bool:
        if relative == ".":
            # "is the repository itself there", which for a fake is whether it
            # was ever declared. The filesystem locator answers the same
            # question with Path(root, ".").exists().
            return repository_root in self._roots
        files = self._files.get(repository_root, {})
        return relative in files or any(name.startswith(f"{relative}/") for name in files)

    def read_text(self, repository_root: PurePath, relative: str) -> str | None:
        return self._files.get(repository_root, {}).get(relative)

    def write_text(self, repository_root: PurePath, relative: str, content: str) -> None:
        self._files.setdefault(repository_root, {})[relative] = content

    # -- test affordances -------------------------------------------------

    def put_raw_marker(self, repository_root: PurePath, text: str) -> None:
        """Place marker content :meth:`write_marker` would never produce."""
        self._markers[repository_root] = text

    def put_file(self, repository_root: PurePath, relative: str, text: str) -> None:
        """Place a file inside a repository that never exists on disk."""
        self._files.setdefault(repository_root, {})[relative] = text
