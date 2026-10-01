"""LocalSecretFileStore -- the protected-file SecretStore.

core/05 section 21 names two implementations: the OS keyring when available,
and a protected local file as fallback. This is the fallback. The local API
credential has to exist before anything can authenticate to the service, on a
machine that may have no keyring daemon running at all.

`details/security-configuration.md` section 4 requires the fallback to "produce a
clear warning", so it does: :attr:`warnings` says plainly that secrets are on
disk rather than in a keyring, and says so again if it had to tighten
permissions somebody else loosened.

The file lives outside the vault by construction -- the caller passes a path
under the platform state directory -- and holds nothing but names and values.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from never4ga.errors import SecretStoreError
from never4ga.ports.secret_store import SecretRef

__all__ = ["LocalSecretFileStore"]

_ENCODING: Final = "utf-8"

#: Owner-only, on both the file and the directory holding it.
_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700

_VERSION: Final = 1

_FALLBACK_WARNING: Final = (
    "secrets are stored in a protected local file rather than an OS keyring; "
    "anything able to read files as this user can read them"
)


class LocalSecretFileStore:
    """A SecretStore backed by one owner-readable JSON file.

    Every operation reads the file and writes it back whole. That is the right
    trade at this size: a handful of credentials, changed rarely, and a process
    that must never serve a value another process has since replaced.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._extra_warnings: list[str] = []
        if path.exists():
            self._enforce_permissions()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def warnings(self) -> tuple[str, ...]:
        """What the caller should tell the user, and never a secret value."""
        return (_FALLBACK_WARNING, *self._extra_warnings)

    def get(self, ref: SecretRef) -> str | None:
        return self._read().get(ref.name)

    def set(self, ref: SecretRef, value: str) -> None:
        secrets = self._read()
        secrets[ref.name] = value
        self._write(secrets)

    def delete(self, ref: SecretRef) -> None:
        secrets = self._read()
        if secrets.pop(ref.name, None) is None:
            return
        self._write(secrets)

    def list_refs(self) -> Sequence[SecretRef]:
        return tuple(SecretRef(name) for name in sorted(self._read()))

    def __repr__(self) -> str:
        try:
            names = ", ".join(sorted(self._read()))
        except SecretStoreError:
            names = "unreadable"
        return f"{type(self).__name__}({self._path}, refs=[{names}])"

    # -- internals --------------------------------------------------------

    def _read(self) -> dict[str, str]:
        try:
            text = self._path.read_text(encoding=_ENCODING)
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise SecretStoreError(f"cannot read secrets at {self._path}: {error}") from error
        return self._parse(text)

    def _parse(self, text: str) -> dict[str, str]:
        """Refuse anything unexpected rather than treat it as empty.

        A store that reads a damaged file as "no secrets" would then write over
        it on the next ``set``, and the credential it replaced is gone. The
        error deliberately quotes nothing from the file.
        """
        try:
            document: Any = json.loads(text)
        except json.JSONDecodeError as error:
            raise SecretStoreError(
                f"the secrets file at {self._path} is not valid JSON "
                f"(line {error.lineno}); nothing was changed"
            ) from error
        if not isinstance(document, dict) or not isinstance(document.get("secrets"), dict):
            raise SecretStoreError(
                f"the secrets file at {self._path} is not in the expected format; "
                "nothing was changed"
            )
        secrets = document["secrets"]
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in secrets.items()
        ):
            raise SecretStoreError(
                f"the secrets file at {self._path} holds a non-string entry; nothing was changed"
            )
        return dict(secrets)

    def _write(self, secrets: dict[str, str]) -> None:
        """Replace the file atomically, and never widen its permissions.

        The temporary file is created in the same directory so the rename
        cannot cross a filesystem, and with owner-only permissions from the
        moment it exists rather than a moment afterwards.
        """
        directory = self._path.parent
        directory.mkdir(parents=True, mode=_DIRECTORY_MODE, exist_ok=True)
        payload = json.dumps({"version": _VERSION, "secrets": secrets}, indent=2, sort_keys=True)

        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".secrets-")
        try:
            os.fchmod(handle, _FILE_MODE)
            with os.fdopen(handle, "w", encoding=_ENCODING) as stream:
                stream.write(payload + "\n")
            os.replace(temporary, self._path)
        except OSError as error:
            Path(temporary).unlink(missing_ok=True)
            raise SecretStoreError(f"cannot write secrets at {self._path}: {error}") from error

    def _enforce_permissions(self) -> None:
        """Tighten a file somebody else loosened, and say that it happened."""
        try:
            mode = stat.S_IMODE(self._path.stat().st_mode)
        except OSError:
            return
        if mode & 0o077:
            self._path.chmod(_FILE_MODE)
            self._extra_warnings.append(
                f"the secrets file at {self._path} was readable beyond its owner "
                f"(mode {mode:04o}); permissions have been tightened, but treat "
                "every secret in it as exposed"
            )
