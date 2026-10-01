"""ExtensionRegistryFile -- the core/09 registry as one JSON file.

Beside `deployments.json` in the platform *state* directory, for the reason
that one is there: durable, machine-local, and nothing can rebuild it. It
holds what `adapters sync --adopt` recorded -- origin, review date and the hash
of what was replaced (core/09 section 13). Without that record an adopted file
would be indistinguishable from one that was always generated.

Storage and filtering only. Planning lives in
:func:`never4ga.domain.extensions.plan_capability_sync`, exactly as it does for
the in-memory fake, so the two cannot disagree about a rule.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from never4ga.domain.extensions import (
    ClientState,
    DeploymentMode,
    DiscoveredCapability,
    ExtensionClass,
    ExtensionRecord,
    OwnershipProof,
    Permissions,
    PortabilityLevel,
    SyncPlan,
    plan_capability_sync,
)
from never4ga.errors import Never4gaError

__all__ = ["ExtensionRegistryError", "ExtensionRegistryFile"]

_ENCODING: Final = "utf-8"
_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700
_VERSION: Final = 1


class ExtensionRegistryError(Never4gaError):
    """The extension registry could not be read or written."""


class ExtensionRegistryFile:
    """An ExtensionRegistry backed by one owner-readable JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def register(self, record: ExtensionRecord) -> None:
        records = self._load()
        records[record.capability_id] = record
        self._save(records)

    def get(self, capability_id: str) -> ExtensionRecord | None:
        return self._load().get(capability_id)

    def list_records(
        self,
        *,
        extension_class: ExtensionClass | None = None,
        deployment_mode: DeploymentMode | None = None,
        client_id: str | None = None,
    ) -> Sequence[ExtensionRecord]:
        records = [
            record
            for record in self._load().values()
            if (extension_class is None or record.extension_class is extension_class)
            and (deployment_mode is None or record.deployment_mode is deployment_mode)
            and (client_id is None or record.is_enabled_for(client_id))
        ]
        records.sort(key=lambda record: record.capability_id)
        return tuple(records)

    def plan_sync(self, client_id: str, discovered: Sequence[DiscoveredCapability]) -> SyncPlan:
        return plan_capability_sync(client_id, self.list_records(), discovered)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path})"

    # -- internals --------------------------------------------------------

    def _load(self) -> dict[str, ExtensionRecord]:
        try:
            text = self._path.read_text(encoding=_ENCODING)
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise ExtensionRegistryError(
                f"cannot read the extension registry at {self._path}: {error}"
            ) from error
        try:
            document: Any = json.loads(text)
        except json.JSONDecodeError as error:
            # Refused rather than read as empty: an empty registry would let
            # the next adoption overwrite the record of the last one.
            raise ExtensionRegistryError(
                f"the extension registry at {self._path} is not valid JSON "
                f"(line {error.lineno}); nothing was changed"
            ) from error
        if not isinstance(document, dict) or not isinstance(document.get("records"), list):
            raise ExtensionRegistryError(
                f"the extension registry at {self._path} is not in the expected format; "
                "nothing was changed"
            )
        records = [self._parse(entry) for entry in document["records"]]
        return {record.capability_id: record for record in records}

    def _save(self, records: dict[str, ExtensionRecord]) -> None:
        payload = {
            "version": _VERSION,
            "records": [self._render(records[key]) for key in sorted(records)],
        }
        self._write(json.dumps(payload, indent=2) + "\n")

    def _parse(self, entry: Any) -> ExtensionRecord:
        if not isinstance(entry, dict):
            raise ExtensionRegistryError(
                f"a record in {self._path} is not an object; nothing was changed"
            )
        try:
            ownership = entry.get("ownership")
            permissions = dict(entry.get("permissions") or {})
            return ExtensionRecord(
                capability_id=str(entry["capability_id"]),
                extension_class=ExtensionClass(entry["extension_class"]),
                title=str(entry.get("title", "")),
                deployment_mode=DeploymentMode(entry["deployment_mode"]),
                portability=PortabilityLevel(entry.get("portability", PortabilityLevel.UNKNOWN)),
                clients={
                    str(client): ClientState(state)
                    for client, state in dict(entry.get("clients") or {}).items()
                },
                permissions=Permissions(**{k: bool(v) for k, v in permissions.items()}),
                ownership=(
                    OwnershipProof(
                        owner=str(ownership["owner"]),
                        content_hash=str(ownership["content_hash"]),
                        generated_at=ownership.get("generated_at"),
                    )
                    if isinstance(ownership, dict)
                    else None
                ),
                origin=str(entry.get("origin", "")),
                adopted_at=str(entry.get("adopted_at", "")),
                replaced_hash=str(entry.get("replaced_hash", "")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ExtensionRegistryError(
                f"a record in {self._path} is malformed: {error}; nothing was changed"
            ) from error

    @staticmethod
    def _render(record: ExtensionRecord) -> dict[str, Any]:
        rendered: dict[str, Any] = {
            "capability_id": record.capability_id,
            "extension_class": record.extension_class.value,
            "title": record.title,
            "deployment_mode": record.deployment_mode.value,
            "portability": record.portability.value,
            "clients": {client: state.value for client, state in sorted(record.clients.items())},
            "permissions": {
                "read_only": record.permissions.read_only,
                "writes_external_state": record.permissions.writes_external_state,
                "filesystem_access": record.permissions.filesystem_access,
                "network_access": record.permissions.network_access,
                "executes_commands": record.permissions.executes_commands,
                "requires_credentials": record.permissions.requires_credentials,
            },
            "ownership": (
                {
                    "owner": record.ownership.owner,
                    "content_hash": record.ownership.content_hash,
                    "generated_at": record.ownership.generated_at,
                }
                if record.ownership is not None
                else None
            ),
            "origin": record.origin,
        }
        # Omitted when empty, so a registry holding nothing adopted reads the
        # same as one written before adoption was recorded.
        if record.adopted_at:
            rendered["adopted_at"] = record.adopted_at
        if record.replaced_hash:
            rendered["replaced_hash"] = record.replaced_hash
        return rendered

    def _write(self, payload: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        handle, temporary = tempfile.mkstemp(dir=self._path.parent, prefix=".extensions-")
        try:
            os.fchmod(handle, _FILE_MODE)
            with os.fdopen(handle, "w", encoding=_ENCODING) as stream:
                stream.write(payload)
            os.replace(temporary, self._path)
        except OSError as error:
            Path(temporary).unlink(missing_ok=True)
            raise ExtensionRegistryError(
                f"cannot write the extension registry at {self._path}: {error}"
            ) from error
