"""In-memory VaultFileStore."""

from __future__ import annotations

from collections.abc import Iterator

from never4ga.domain.document import VaultPath

__all__ = ["InMemoryVaultFileStore"]


class InMemoryVaultFileStore:
    """A VaultFileStore backed by dictionaries.

    Directories are tracked explicitly rather than implied by their contents, so
    an empty root -- which core/01 section 1 requires to exist -- is
    representable.
    """

    def __init__(self) -> None:
        self._files: dict[VaultPath, str] = {}
        self._directories: set[VaultPath] = set()

    def exists(self, path: VaultPath) -> bool:
        return path in self._files or path in self._directories

    def is_directory(self, path: VaultPath) -> bool:
        return path in self._directories

    def ensure_directory(self, path: VaultPath) -> None:
        for depth in range(1, len(path.segments) + 1):
            self._directories.add(VaultPath(path.segments[:depth]))

    def read_text(self, path: VaultPath) -> str | None:
        return self._files.get(path)

    def write_text(self, path: VaultPath, text: str) -> None:
        if len(path.segments) > 1:
            self.ensure_directory(VaultPath(path.segments[:-1]))
        self._files[path] = text

    def remove(self, path: VaultPath) -> None:
        self._files.pop(path, None)

    def iter_paths(self) -> Iterator[VaultPath]:
        return iter(sorted(self._files))

    def iter_directories(self) -> Iterator[VaultPath]:
        return iter(sorted(self._directories))
