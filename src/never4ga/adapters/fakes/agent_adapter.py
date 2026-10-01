"""Fake AgentAdapter -- reports a fixed set of discovered capabilities."""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.extensions import DiscoveredCapability, ExtensionClass
from never4ga.ports.agent_adapter import AdapterDescription

__all__ = ["FakeAgentAdapter"]


class FakeAgentAdapter:
    def __init__(
        self,
        client_id: str = "fake_client",
        discovered: Sequence[DiscoveredCapability] = (),
        supported_classes: frozenset[ExtensionClass] | None = None,
    ) -> None:
        self._client_id = client_id
        self._discovered = tuple(discovered)
        self._supported = supported_classes or frozenset(
            {ExtensionClass.SKILL, ExtensionClass.MCP_SERVER}
        )

    @property
    def client_id(self) -> str:
        return self._client_id

    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            client_id=self._client_id,
            display_name=self._client_id.replace("_", " ").title(),
            supported_classes=self._supported,
        )

    def discover(self) -> Sequence[DiscoveredCapability]:
        return self._discovered
