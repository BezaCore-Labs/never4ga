"""ClientFileStore over the real filesystem.

Deliberately dull. The only judgement here is what counts as "not text": a
binary file under a Skill directory is reported by name with empty content
rather than skipped, because its presence is what decides whether a directory is
ours to overwrite, and a store that silently omitted it would let sync overwrite
something it had never seen.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from never4ga.errors import Never4gaError

__all__ = ["FileSystemClientFileStore"]

_ENCODING: Final = "utf-8"
_DIRECTORY_MODE: Final = 0o755


class ClientFileStoreError(Never4gaError):
    """A client's own directory could not be read or written."""


class FileSystemClientFileStore:
    def exists(self, root: Path) -> bool:
        return root.exists() or root.is_symlink()

    def is_symlink(self, root: Path) -> bool:
        return root.is_symlink()

    def read_tree(self, root: Path) -> Mapping[str, str] | None:
        if not root.exists():
            return None
        if root.is_file():
            return {root.name: self._read(root)}
        files: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[path.relative_to(root).as_posix()] = self._read(path)
        return files

    def write_tree(self, root: Path, files: Mapping[str, str]) -> None:
        self.remove_tree(root)
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
            target.write_text(content, encoding=_ENCODING)

    def link_tree(self, root: Path, source: Path) -> None:
        self.remove_tree(root)
        root.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        try:
            root.symlink_to(source, target_is_directory=True)
        except OSError as error:
            # Windows without developer mode, and any filesystem that has no
            # links. The descriptor asked for something this machine cannot do.
            raise ClientFileStoreError(
                f'cannot link {root} to {source}: {error}; use deployment = "copy"'
            ) from error

    def remove_tree(self, root: Path) -> None:
        if root.is_symlink() or root.is_file():
            root.unlink(missing_ok=True)
        elif root.is_dir():
            shutil.rmtree(root)

    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding=_ENCODING)
        except UnicodeDecodeError:
            # Present, and not something we can compare or reproduce as text.
            return ""
        except OSError as error:
            raise ClientFileStoreError(f"cannot read {path}: {error}") from error
