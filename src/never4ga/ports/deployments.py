"""DeploymentStore -- the ownership manifest of core/04 section 25.

Durable machine-local state, and a port for the same reason
:class:`~never4ga.ports.workspace_mappings.WorkspaceMappingStore` is one:
nothing can rebuild it. The vault holds the canonical Skills and deliberately
does not know what is installed on any particular machine, so if this record is
lost, every deployed copy becomes unmanaged -- and an unmanaged copy is one
Never4gA may never overwrite or remove.

Whole-set, like the mappings. There are a few dozen entries, the writer is the
CLI in process, and read-modify-write belongs to the service above.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from never4ga.domain.deployments import Deployment

__all__ = ["DeploymentStore"]


@runtime_checkable
class DeploymentStore(Protocol):
    def load(self) -> Sequence[Deployment]:
        """Everything this machine has deployed. Empty when nothing has been.

        Must not create anything: asking what is deployed is a read.
        """
        ...

    def save(self, deployments: Sequence[Deployment]) -> None:
        """Replace the whole set. Saving nothing empties the manifest."""
        ...
