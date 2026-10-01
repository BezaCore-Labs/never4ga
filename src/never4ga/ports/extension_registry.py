"""ExtensionRegistry -- portable capability governance (core/09).

This port is the data model and planning contract only. Applying a plan to a
client's native configuration belongs to the agent adapters, which is why this
port plans but never writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from never4ga.domain.extensions import (
    DeploymentMode,
    DiscoveredCapability,
    ExtensionClass,
    ExtensionRecord,
    SyncPlan,
)

__all__ = ["ExtensionRegistry"]


@runtime_checkable
class ExtensionRegistry(Protocol):
    def register(self, record: ExtensionRecord) -> None: ...

    def get(self, capability_id: str) -> ExtensionRecord | None: ...

    def list_records(
        self,
        *,
        extension_class: ExtensionClass | None = None,
        deployment_mode: DeploymentMode | None = None,
        client_id: str | None = None,
    ) -> Sequence[ExtensionRecord]: ...

    def plan_sync(
        self,
        client_id: str,
        discovered: Sequence[DiscoveredCapability],
    ) -> SyncPlan:
        """Describe what a sync would do, without doing any of it.

        The plan must preserve every discovered capability Never4gA does not
        own, and must never remove anything without ownership proof.
        """
        ...
