"""In-memory SecretStore."""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.ports.secret_store import SecretRef

__all__ = ["InMemorySecretStore"]


class InMemorySecretStore:
    """A SecretStore held in process memory.

    ``__repr__`` renders reference names only. A secret that reaches a traceback
    or a log line has already leaked, so the redaction lives in the type rather
    than in every call site.
    """

    def __init__(self) -> None:
        self._values: dict[SecretRef, str] = {}

    def get(self, ref: SecretRef) -> str | None:
        return self._values.get(ref)

    def set(self, ref: SecretRef, value: str) -> None:
        self._values[ref] = value

    def delete(self, ref: SecretRef) -> None:
        self._values.pop(ref, None)

    def list_refs(self) -> Sequence[SecretRef]:
        return tuple(sorted(self._values))

    def __repr__(self) -> str:
        names = ", ".join(ref.name for ref in sorted(self._values))
        return f"{type(self).__name__}(refs=[{names}])"
