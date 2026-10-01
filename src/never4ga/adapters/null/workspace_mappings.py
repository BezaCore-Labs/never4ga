"""NullWorkspaceMappingStore -- a directory that is not a vault maps nothing.

Mappings are kept per vault, addressed by the vault's identity. A
directory without a system manifest has no identity, so there is nowhere its
mappings could live: it holds none, and a write is refused rather than put
somewhere another vault would read it.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.scope import WorkspaceMapping
from never4ga.errors import WorkspaceMappingStoreError

__all__ = ["NullWorkspaceMappingStore"]


class NullWorkspaceMappingStore:
    def load(self) -> Sequence[WorkspaceMapping]:
        return ()

    def save(self, mappings: Sequence[WorkspaceMapping]) -> None:
        raise WorkspaceMappingStoreError(
            "this is not an initialized vault, so it has nowhere to keep mappings; "
            "run `never4ga init` first"
        )
