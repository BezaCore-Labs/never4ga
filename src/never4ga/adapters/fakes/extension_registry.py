"""In-memory ExtensionRegistry.

Storage and filtering only. The planning rules live in
:func:`never4ga.domain.extensions.plan_capability_sync`: rules kept in a fake
would be a property of the test double rather than of the system, and a service
may not import an adapter to reach them.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.extensions import (
    DeploymentMode,
    DiscoveredCapability,
    ExtensionClass,
    ExtensionRecord,
    SyncPlan,
    plan_capability_sync,
)

__all__ = ["InMemoryExtensionRegistry"]


class InMemoryExtensionRegistry:
    def __init__(self) -> None:
        self._records: dict[str, ExtensionRecord] = {}

    def register(self, record: ExtensionRecord) -> None:
        self._records[record.capability_id] = record

    def get(self, capability_id: str) -> ExtensionRecord | None:
        return self._records.get(capability_id)

    def list_records(
        self,
        *,
        extension_class: ExtensionClass | None = None,
        deployment_mode: DeploymentMode | None = None,
        client_id: str | None = None,
    ) -> Sequence[ExtensionRecord]:
        records = [
            record
            for record in self._records.values()
            if (extension_class is None or record.extension_class is extension_class)
            and (deployment_mode is None or record.deployment_mode is deployment_mode)
            and (client_id is None or record.is_enabled_for(client_id))
        ]
        records.sort(key=lambda record: record.capability_id)
        return tuple(records)

    def plan_sync(
        self,
        client_id: str,
        discovered: Sequence[DiscoveredCapability],
    ) -> SyncPlan:
        return plan_capability_sync(client_id, self.list_records(), discovered)
