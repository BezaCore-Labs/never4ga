"""In-memory WorkspaceMappingStore.

Lets workspace resolution be tested without a filesystem, which is what keeps
the services above it honest about depending on the port rather than on a file.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.scope import WorkspaceMapping

__all__ = ["InMemoryWorkspaceMappingStore"]


class InMemoryWorkspaceMappingStore:
    def __init__(self, mappings: Sequence[WorkspaceMapping] = ()) -> None:
        self._mappings: tuple[WorkspaceMapping, ...] = tuple(mappings)

    def load(self) -> Sequence[WorkspaceMapping]:
        return self._mappings

    def save(self, mappings: Sequence[WorkspaceMapping]) -> None:
        self._mappings = tuple(mappings)
