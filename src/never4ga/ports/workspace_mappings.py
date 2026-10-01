"""WorkspaceMappingStore -- one vault's repo-to-workspace map on this machine.

`core/04` section 13 and `core/05` section 9 both put repo mappings in
machine-local configuration. That configuration is TOML the user owns and
Never4gA never writes, so the mappings live somewhere else rather than weaken
the rule: a file Never4gA owns, outside the vault.

**One vault's, not the machine's.** A single file shared by every vault on the
machine would let a repository mapped from one vault resolve from another. A
store is always a single vault's, and composition is where that is decided.

**Durable, not derived.** Everything else Never4gA writes outside the vault can
be rebuilt by reindexing. This cannot: the vault holds workspace identity, and
deliberately does not know where a repository sits on any particular machine.
That asymmetry is why this is a port with a real backing file rather than a
cache, and why the contract's central property is that a mapping round-trips
unchanged.

The interface is deliberately whole-set. There are a handful of mappings, they
change rarely, and the writer is the CLI in process, so read-modify-write
belongs to the service above, and an implementation never has to reconcile a
partial update.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from never4ga.domain.scope import WorkspaceMapping

__all__ = ["WorkspaceMappingStore"]


@runtime_checkable
class WorkspaceMappingStore(Protocol):
    def load(self) -> Sequence[WorkspaceMapping]:
        """Every mapping this machine knows. Empty when nothing has been saved.

        Must not create anything: asking what is mapped is a read.
        """
        ...

    def save(self, mappings: Sequence[WorkspaceMapping]) -> None:
        """Replace the whole set. Saving nothing empties the store."""
        ...
