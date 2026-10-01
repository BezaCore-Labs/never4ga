"""WorkspaceMappingFile -- the repo-to-workspace map as one JSON file.

Where it lives is :meth:`~never4ga.platform_paths.PlatformPaths.workspaces_file`,
in the vault's own directory beside its session store. Durable rather than
derived, like the sessions: nothing can rebuild the mappings, since the vault
does not know where a repository sits on this machine.

JSON rather than TOML, deliberately. Local files are split by *who owns the
file*, not by what it holds: files the user hand-edits are TOML under the config
directory, and files Never4gA writes are JSON, like ``secrets.json``. Writing
TOML here would put a machine-written file in the shape of a hand-edited one.

**One file per vault.** :class:`VaultWorkspaceMappings` is what a composition
root hands out: this vault's own file, plus whatever of the older machine-wide
file this vault can prove is its own.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath
from typing import Any, Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import WorkspaceMapping
from never4ga.errors import WorkspaceMappingStoreError

__all__ = ["VaultWorkspaceMappings", "WorkspaceMappingFile"]

_ENCODING: Final = "utf-8"

#: Owner-only. The file describes where this user's repositories sit on disk,
#: which is nobody else's business even though it holds no secret.
_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700

_VERSION: Final = 1


class WorkspaceMappingFile:
    """A WorkspaceMappingStore backed by one owner-readable JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Sequence[WorkspaceMapping]:
        try:
            text = self._path.read_text(encoding=_ENCODING)
        except FileNotFoundError:
            return ()
        except OSError as error:
            raise WorkspaceMappingStoreError(
                f"cannot read workspace mappings at {self._path}: {error}"
            ) from error
        return tuple(self._parse(entry) for entry in self._entries(text))

    def save(self, mappings: Sequence[WorkspaceMapping]) -> None:
        payload = {
            "version": _VERSION,
            "workspaces": [
                self._render(mapping)
                for mapping in sorted(mappings, key=lambda m: str(m.workspace_id))
            ],
        }
        self._write(json.dumps(payload, indent=2) + "\n")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path})"

    # -- internals --------------------------------------------------------

    def _entries(self, text: str) -> list[Any]:
        """Refuse anything unexpected rather than treat it as empty.

        A store that read a damaged file as "no mappings" would write over it on
        the next save, and the mappings it replaced cannot be rebuilt from
        anywhere.
        """
        try:
            document: Any = json.loads(text)
        except json.JSONDecodeError as error:
            raise WorkspaceMappingStoreError(
                f"the workspace mappings at {self._path} are not valid JSON "
                f"(line {error.lineno}); nothing was changed"
            ) from error
        if not isinstance(document, dict) or not isinstance(document.get("workspaces"), list):
            raise WorkspaceMappingStoreError(
                f"the workspace mappings at {self._path} are not in the expected "
                "format; nothing was changed"
            )
        return list(document["workspaces"])

    def _parse(self, entry: Any) -> WorkspaceMapping:
        if not isinstance(entry, dict):
            raise WorkspaceMappingStoreError(
                f"a workspace entry in {self._path} is not an object; nothing was changed"
            )
        try:
            repository_root = entry.get("repository_root")
            parent = entry.get("parent")
            return WorkspaceMapping(
                workspace_id=ConceptId.parse(str(entry["id"])),
                workspace_path=VaultPath.parse(str(entry["path"])),
                repository_root=None if repository_root is None else PurePath(str(repository_root)),
                parent_id=None if parent is None else ConceptId.parse(str(parent)),
                ships_agent_contract=bool(entry.get("ships_agent_contract", False)),
            )
        except KeyError as error:
            raise WorkspaceMappingStoreError(
                f"a workspace entry in {self._path} is missing {error}; nothing was changed"
            ) from error
        except ValueError as error:
            # IdentityError and VaultPathError are both ValueErrors: a malformed
            # UUID or a path outside the vault arrives here rather than escaping
            # as something a caller would not associate with this file.
            raise WorkspaceMappingStoreError(
                f"a workspace entry in {self._path} is malformed: {error}; nothing was changed"
            ) from error

    def _render(self, mapping: WorkspaceMapping) -> dict[str, str | bool | None]:
        return {
            "id": str(mapping.workspace_id),
            "path": str(mapping.workspace_path),
            "repository_root": (
                None if mapping.repository_root is None else str(mapping.repository_root)
            ),
            "parent": None if mapping.parent_id is None else str(mapping.parent_id),
            "ships_agent_contract": mapping.ships_agent_contract,
        }

    def _write(self, payload: str) -> None:
        """Replace the file atomically, and never widen its permissions.

        The temporary file is created in the same directory so the rename cannot
        cross a filesystem, and owner-only from the moment it exists.
        """
        directory = self._path.parent
        directory.mkdir(parents=True, mode=_DIRECTORY_MODE, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".workspaces-")
        try:
            os.fchmod(handle, _FILE_MODE)
            with os.fdopen(handle, "w", encoding=_ENCODING) as stream:
                stream.write(payload)
            os.replace(temporary, self._path)
        except OSError as error:
            Path(temporary).unlink(missing_ok=True)
            raise WorkspaceMappingStoreError(
                f"cannot write workspace mappings at {self._path}: {error}"
            ) from error


class VaultWorkspaceMappings:
    """One vault's mappings, adopting its share of the machine-wide file.

    Older versions kept one machine-wide file for every vault, and its entries
    do not say which vault wrote them. So ownership is established the
    only way it can be: ``claims`` says whether this vault holds the workspace
    an entry names, and an entry is this vault's exactly when it does. An entry
    no vault claims stays where it is, harmlessly, until its own vault opens.

    **Reading moves nothing.** :meth:`load` returns this vault's own entries and
    the ones it claims; nothing is written merely by asking. :meth:`save` writes
    this vault's file and takes the claimed entries out of the machine-wide
    one, so the first mapping change completes the move, and an unmapped entry
    cannot come back from the old file. The old file is removed once empty.

    Where both hold an entry for the same repository, this vault's own wins: it
    can only have been written after the move began. A service still running
    an older build is how the old file could gain one after that.
    """

    def __init__(
        self,
        path: Path,
        *,
        machine: Path,
        claims: Callable[[WorkspaceMapping], bool],
    ) -> None:
        self._own = WorkspaceMappingFile(path)
        self._machine = WorkspaceMappingFile(machine)
        self._claims = claims

    @property
    def path(self) -> Path:
        return self._own.path

    def load(self) -> Sequence[WorkspaceMapping]:
        own = tuple(self._own.load())
        held = {_slot(mapping) for mapping in own}
        adopted = tuple(m for m in self._claimed(self._machine.load()) if _slot(m) not in held)
        return own + adopted

    def save(self, mappings: Sequence[WorkspaceMapping]) -> None:
        self._own.save(mappings)
        machine = tuple(self._machine.load())
        if not machine:
            return
        claimed = set(self._claimed(machine))
        if not claimed:
            return
        remaining = [mapping for mapping in machine if mapping not in claimed]
        if remaining:
            self._machine.save(remaining)
            return
        try:
            self._machine.path.unlink(missing_ok=True)
        except OSError as error:
            raise WorkspaceMappingStoreError(
                f"cannot remove the emptied workspace mappings at {self._machine.path}: {error}"
            ) from error

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._own.path}, machine={self._machine.path})"

    def _claimed(self, machine: Sequence[WorkspaceMapping]) -> tuple[WorkspaceMapping, ...]:
        return tuple(mapping for mapping in machine if self._claims(mapping))


def _slot(mapping: WorkspaceMapping) -> PurePath | ConceptId:
    """What one entry occupies: its repository, or its workspace if it has none.

    The same rule `WorkspaceService.map` replaces by, so a newer entry displaces
    an older one here exactly when a re-map would have.
    """
    return mapping.repository_root if mapping.repository_root is not None else mapping.workspace_id
