"""AgentAdapter -- one coding-agent client (core/09 sections 10-12).

Each adapter owns its client's native configuration format. Never4gA does not
invent one config file and symlink it everywhere (core/09 section 10).

This port covers description and discovery only. Discovery output is derived
local state, never canonical (section 12).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from never4ga.domain.extensions import DiscoveredCapability, ExtensionClass

__all__ = ["AdapterDescription", "AgentAdapter"]


@dataclass(frozen=True, slots=True)
class AdapterDescription:
    client_id: str
    display_name: str
    supported_classes: frozenset[ExtensionClass]


@runtime_checkable
class AgentAdapter(Protocol):
    @property
    def client_id(self) -> str: ...

    def describe(self) -> AdapterDescription: ...

    def discover(self) -> Sequence[DiscoveredCapability]:
        """Report what is installed in this client. Derived state only."""
        ...
