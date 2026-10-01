"""ClientFileStore -- the filesystem outside the vault.

Every other store here addresses vault content. This one addresses a client's
own configuration directory, which is somebody else's territory: Never4gA reads
it to find out what is there, and writes only what it can prove it owns.

It exists for the reason :class:`~never4ga.ports.vault_files.VaultFileStore`
does -- so the sync engine is written against an interface and stays testable --
and its operations are whole-tree rather than per-file because a Skill is a
directory (core/04 section 20), and half a deployed Skill is worse than none.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["ClientFileStore"]


@runtime_checkable
class ClientFileStore(Protocol):
    def exists(self, root: Path) -> bool: ...

    def is_symlink(self, root: Path) -> bool:
        """Whether this location is a link rather than a directory of its own."""
        ...

    def read_tree(self, root: Path) -> Mapping[str, str] | None:
        """Every text file under ``root``, keyed by path relative to it.

        ``None`` when there is nothing there. A file that is not text is
        reported by name with empty content rather than skipped: its presence
        still means the directory is not ours to overwrite.
        """
        ...

    def write_tree(self, root: Path, files: Mapping[str, str]) -> None:
        """Replace ``root`` with exactly these files."""
        ...

    def link_tree(self, root: Path, source: Path) -> None:
        """Make ``root`` a link to ``source``."""
        ...

    def remove_tree(self, root: Path) -> None:
        """Delete ``root``. The caller has already proven it owns it."""
        ...
