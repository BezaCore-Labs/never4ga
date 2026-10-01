"""DeploymentFile -- the ownership manifest as one JSON file.

Beside `secrets.json` in the platform *state* directory, and JSON for the
same reason: files Never4gA itself writes are JSON under state.

State rather than data because it is durable. If this file is lost, every Skill
Never4gA has deployed becomes unmanaged -- and `core/04` section 25 forbids
overwriting or removing an unmanaged Skill, so losing it is not a cache miss but
a permanent handover of everything already deployed.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from never4ga.domain.clients import DeploymentMechanism
from never4ga.domain.deployments import Deployment
from never4ga.errors import Never4gaError

__all__ = ["DeploymentFile", "DeploymentStoreError"]

_ENCODING: Final = "utf-8"
_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700
_VERSION: Final = 1


class DeploymentStoreError(Never4gaError):
    """The ownership manifest could not be read or written."""


class DeploymentFile:
    """A DeploymentStore backed by one owner-readable JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Sequence[Deployment]:
        try:
            text = self._path.read_text(encoding=_ENCODING)
        except FileNotFoundError:
            return ()
        except OSError as error:
            raise DeploymentStoreError(
                f"cannot read the ownership manifest at {self._path}: {error}"
            ) from error
        return tuple(self._parse(entry) for entry in self._entries(text))

    def save(self, deployments: Sequence[Deployment]) -> None:
        payload = {
            "version": _VERSION,
            "deployments": [self._render(item) for item in sorted(deployments, key=_order)],
        }
        self._write(json.dumps(payload, indent=2) + "\n")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path})"

    # -- internals --------------------------------------------------------

    def _entries(self, text: str) -> list[Any]:
        """Refuse a damaged file rather than read it as empty.

        Reading a corrupt manifest as "nothing is deployed" would make every
        deployed Skill unmanaged at once, and the next sync would report a
        collision for every one of them.
        """
        try:
            document: Any = json.loads(text)
        except json.JSONDecodeError as error:
            raise DeploymentStoreError(
                f"the ownership manifest at {self._path} is not valid JSON "
                f"(line {error.lineno}); nothing was changed"
            ) from error
        if not isinstance(document, dict) or not isinstance(document.get("deployments"), list):
            raise DeploymentStoreError(
                f"the ownership manifest at {self._path} is not in the expected "
                "format; nothing was changed"
            )
        return list(document["deployments"])

    def _parse(self, entry: Any) -> Deployment:
        if not isinstance(entry, dict):
            raise DeploymentStoreError(
                f"a deployment entry in {self._path} is not an object; nothing was changed"
            )
        try:
            return Deployment(
                client_id=str(entry["client_id"]),
                name=str(entry["name"]),
                target=str(entry["target"]),
                source_path=str(entry["source_path"]),
                source_hash=str(entry["source_hash"]),
                deployed_hash=str(entry["deployed_hash"]),
                mechanism=DeploymentMechanism(entry.get("mechanism", "copy")),
                deployed_at=str(entry.get("deployed_at", "")),
                # Absent from older manifests, which must keep loading:
                # refusing one would make every deployed Skill unmanaged at
                # once.
                files={str(k): str(v) for k, v in (entry.get("files") or {}).items()},
            )
        except KeyError as error:
            raise DeploymentStoreError(
                f"a deployment entry in {self._path} is missing {error}; nothing was changed"
            ) from error
        except ValueError as error:
            raise DeploymentStoreError(
                f"a deployment entry in {self._path} is malformed: {error}; nothing was changed"
            ) from error

    def _render(self, item: Deployment) -> dict[str, Any]:
        rendered: dict[str, Any] = {
            "client_id": item.client_id,
            "name": item.name,
            "target": item.target,
            "source_path": item.source_path,
            "source_hash": item.source_hash,
            "deployed_hash": item.deployed_hash,
            "mechanism": item.mechanism.value,
            "deployed_at": item.deployed_at,
        }
        # Omitted when empty so a manifest holding nothing new stays
        # byte-identical to what an older build would have written.
        if item.files:
            rendered["files"] = dict(sorted(item.files.items()))
        return rendered

    def _write(self, payload: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        handle, temporary = tempfile.mkstemp(dir=self._path.parent, prefix=".deployments-")
        try:
            os.fchmod(handle, _FILE_MODE)
            with os.fdopen(handle, "w", encoding=_ENCODING) as stream:
                stream.write(payload)
            os.replace(temporary, self._path)
        except OSError as error:
            Path(temporary).unlink(missing_ok=True)
            raise DeploymentStoreError(
                f"cannot write the ownership manifest at {self._path}: {error}"
            ) from error


def _order(item: Deployment) -> tuple[str, str]:
    return (item.client_id, item.name)
