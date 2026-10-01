"""FileSystemVaultFileStore -- vault files and directories on disk."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.errors import VaultPathError

__all__ = ["FileSystemVaultFileStore"]

_ENCODING: Final = "utf-8"


class FileSystemVaultFileStore:
    """A VaultFileStore rooted at a directory on disk."""

    def __init__(self, root: Path) -> None:
        if not root.exists():
            raise VaultPathError(f"vault root {root} does not exist")
        if not root.is_dir():
            raise VaultPathError(f"vault root {root} is not a directory")
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def exists(self, path: VaultPath) -> bool:
        return self._absolute(path).exists()

    def is_directory(self, path: VaultPath) -> bool:
        return self._absolute(path).is_dir()

    def ensure_directory(self, path: VaultPath) -> None:
        self._absolute(path).mkdir(parents=True, exist_ok=True)

    def read_text(self, path: VaultPath) -> str | None:
        absolute = self._absolute(path)
        if not absolute.is_file():
            return None
        return absolute.read_text(encoding=_ENCODING)

    def write_text(self, path: VaultPath, text: str) -> None:
        absolute = self._absolute(path)
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(text, encoding=_ENCODING, newline="")

    def remove(self, path: VaultPath) -> None:
        target = self._absolute(path)
        if target.is_dir():
            raise VaultPathError(f"{path} is a directory; this removes files only")
        target.unlink(missing_ok=True)

    def iter_paths(self) -> Iterator[VaultPath]:
        for directory, _, filenames in self._walk():
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                yield self._relative(Path(directory) / filename)

    def iter_directories(self) -> Iterator[VaultPath]:
        for directory, subdirectories, _ in self._walk():
            for name in subdirectories:
                yield self._relative(Path(directory) / name)

    # -- internals --------------------------------------------------------

    def _walk(self) -> Iterator[tuple[str, list[str], list[str]]]:
        for directory, subdirectories, filenames in os.walk(self._root, followlinks=False):
            subdirectories[:] = sorted(
                name
                for name in subdirectories
                if not name.startswith(".") and not (Path(directory) / name).is_symlink()
            )
            yield directory, subdirectories, filenames

    def _absolute(self, path: VaultPath) -> Path:
        resolved = self._root.joinpath(*path.segments)
        if not self._is_inside(resolved):
            raise VaultPathError(f"{path} resolves outside the vault root")
        return resolved

    def _is_inside(self, candidate: Path) -> bool:
        try:
            existing = candidate
            while not existing.exists():
                parent = existing.parent
                if parent == existing:
                    return True
                existing = parent
            return existing.resolve().is_relative_to(self._root)
        except OSError:
            return False

    def _relative(self, absolute: Path) -> VaultPath:
        return VaultPath(absolute.relative_to(self._root).parts)
