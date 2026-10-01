"""VaultFileStore -- vault content that is not a canonical concept.

``DocumentStore`` addresses canonical concepts by identity. A vault also contains
files that have no identity and never will:

- reserved OKF navigation and history (``index.md``, ``log.md``);
- templates under ``50_System/Templates/``;
- foreign-format, tool-owned files such as Obsidian ``.base`` views
  (core/02 section 3.3);
- the directories themselves, which must exist even while empty because the
  seven roots are part of the contract (core/01 section 1).

core/05 section 20 does not name this port, because it lists the *storage and
retrieval* boundaries where a backend could later be swapped. This one exists
so that vault initialization, workspace scaffolding and ``doctor`` are
written against an interface and stay testable without a temporary directory.

Implementations must reject any path escaping the vault root.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from never4ga.domain.document import VaultPath

__all__ = ["VaultFileStore"]


@runtime_checkable
class VaultFileStore(Protocol):
    """Raw, path-addressed access to vault files and directories."""

    def exists(self, path: VaultPath) -> bool:
        """Is there a file or directory at this location?"""
        ...

    def is_directory(self, path: VaultPath) -> bool:
        """Is this location a directory?"""
        ...

    def ensure_directory(self, path: VaultPath) -> None:
        """Create the directory and any missing parents. Idempotent."""
        ...

    def read_text(self, path: VaultPath) -> str | None:
        """Return the file's contents, or ``None`` if it is not there."""
        ...

    def write_text(self, path: VaultPath, text: str) -> None:
        """Create or replace a file, creating parent directories as needed.

        Callers that must not clobber a user's file check :meth:`exists` first;
        Never4gA never overwrites content it does not own (core/09 section 11).
        """
        ...

    def remove(self, path: VaultPath) -> None:
        """Delete one file. Missing is not an error; the end state is the same.

        Directories are deliberately out of scope: the roots are part of the
        contract and nothing may remove one.
        """
        ...

    def iter_paths(self) -> Iterator[VaultPath]:
        """Every file in the vault, excluding dot directories."""
        ...

    def iter_directories(self) -> Iterator[VaultPath]:
        """Every directory in the vault, excluding dot directories."""
        ...
