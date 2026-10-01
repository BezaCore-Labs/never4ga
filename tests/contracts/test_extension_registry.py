"""Run the ExtensionRegistry contract against the in-memory fake and the JSON file."""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.fakes import FakeAgentAdapter, InMemoryExtensionRegistry
from never4ga.adapters.filesystem import ExtensionRegistryError, ExtensionRegistryFile
from never4ga.domain.extensions import (
    ClientState,
    DeploymentMode,
    DiscoveredCapability,
    ExtensionClass,
    ExtensionRecord,
    OwnershipProof,
    Permissions,
    PortabilityLevel,
)
from never4ga.ports.agent_adapter import AgentAdapter
from never4ga.ports.extension_registry import ExtensionRegistry
from tests.contracts.extension_registry_contract import ExtensionRegistryContract, managed_skill

pytestmark = pytest.mark.contract


class TestInMemoryExtensionRegistry(ExtensionRegistryContract):
    @pytest.fixture
    def registry(self) -> ExtensionRegistry:
        return InMemoryExtensionRegistry()


class TestExtensionRegistryFile(ExtensionRegistryContract):
    """The same contract against the JSON file `adapters sync --adopt` writes to."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> ExtensionRegistry:
        return ExtensionRegistryFile(tmp_path / "state" / "extensions.json")

    def test_an_adoption_round_trips_whole(self, tmp_path: Path) -> None:
        # core/09 section 13's fields, plus permissions, plus the clients map:
        # every one has to survive the file, or the record says less after a
        # restart than it did when it was written.
        path = tmp_path / "extensions.json"
        record = ExtensionRecord(
            capability_id="pointer:/src/thing/AGENTS.md",
            extension_class=ExtensionClass.RULE,
            title="AGENTS.md",
            deployment_mode=DeploymentMode.ADOPTED,
            portability=PortabilityLevel.ADAPTABLE,
            clients={"claude_code": ClientState.ENABLED, "codex_cli": ClientState.DISABLED},
            permissions=Permissions(read_only=True, filesystem_access=True),
            ownership=OwnershipProof(
                owner="never4ga", content_hash="b" * 32, generated_at="2026-09-11T20:00:00Z"
            ),
            origin="/src/thing/AGENTS.md",
            adopted_at="2026-09-11T20:00:00Z",
            replaced_hash="c" * 32,
        )
        ExtensionRegistryFile(path).register(record)
        assert ExtensionRegistryFile(path).get(record.capability_id) == record

    def test_a_damaged_file_is_refused_not_read_as_empty(self, tmp_path: Path) -> None:
        # An empty registry would let the next adoption overwrite the record
        # of the last one, which is the loss the file exists to prevent.
        path = tmp_path / "extensions.json"
        path.write_text("{not json")
        with pytest.raises(ExtensionRegistryError):
            ExtensionRegistryFile(path).list_records()

    def test_it_is_owner_readable_only(self, tmp_path: Path) -> None:
        path = tmp_path / "state" / "extensions.json"
        ExtensionRegistryFile(path).register(managed_skill())
        assert path.stat().st_mode & 0o777 == 0o600


class TestAgentAdapterDiscovery:
    """core/09 section 12: discovery is derived state, never canonical."""

    @pytest.fixture
    def adapter(self) -> AgentAdapter:
        return FakeAgentAdapter(
            client_id="claude_code",
            discovered=(
                DiscoveredCapability(
                    client_id="claude_code",
                    extension_class=ExtensionClass.SKILL,
                    name="someones-own-skill",
                    fingerprint="abc",
                ),
            ),
        )

    def test_describes_itself(self, adapter: AgentAdapter) -> None:
        description = adapter.describe()
        assert description.client_id == "claude_code"
        assert ExtensionClass.SKILL in description.supported_classes

    def test_discovered_capabilities_default_to_unowned(self, adapter: AgentAdapter) -> None:
        (found,) = adapter.discover()
        assert found.owner is None
        assert not found.is_never4ga_owned

    def test_discovery_does_not_register_anything(self, adapter: AgentAdapter) -> None:
        registry = InMemoryExtensionRegistry()
        discovered = adapter.discover()
        assert registry.list_records() == ()
        plan = registry.plan_sync(adapter.client_id, discovered=discovered)
        assert registry.get("someones-own-skill") is None
        assert plan.removals == ()
