"""SecretStore -- credentials, always outside the vault.

core/05 sections 7 and 9: secrets never live in committed Markdown. Canonical
documents may reference a secret *by name*; only the machine-local secret store
holds the value. The v0.1 implementation is the OS keyring with a protected
local file fallback (core/05 section 21).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["SecretRef", "SecretStore"]


@dataclass(frozen=True, slots=True, order=True)
class SecretRef:
    """A name that may safely appear in canonical Markdown and in logs.

    It deliberately carries no value field, so a reference cannot accidentally
    be serialised with its secret attached.
    """

    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("secret reference requires a name")

    def __str__(self) -> str:
        return self.name


@runtime_checkable
class SecretStore(Protocol):
    def get(self, ref: SecretRef) -> str | None: ...

    def set(self, ref: SecretRef, value: str) -> None: ...

    def delete(self, ref: SecretRef) -> None: ...

    def list_refs(self) -> Sequence[SecretRef]:
        """Names only. Implementations must never render values in repr or logs."""
        ...
