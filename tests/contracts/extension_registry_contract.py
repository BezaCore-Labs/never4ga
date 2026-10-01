"""ExtensionRegistry contract (core/09).

The sync model cannot claim ownership of unknown configuration by default.
"""

from __future__ import annotations

import pytest

from never4ga.domain.extensions import (
    ClientState,
    DeploymentMode,
    DiscoveredCapability,
    ExtensionClass,
    ExtensionRecord,
    OwnershipProof,
    Permissions,
    PortabilityLevel,
    SyncActionKind,
)
from never4ga.ports.extension_registry import ExtensionRegistry

OWNERSHIP = OwnershipProof(owner="never4ga", content_hash="a" * 64)


def managed_skill(capability_id: str = "never4ga-startup") -> ExtensionRecord:
    return ExtensionRecord(
        capability_id=capability_id,
        extension_class=ExtensionClass.SKILL,
        title="Never4gA Startup",
        deployment_mode=DeploymentMode.MANAGED,
        portability=PortabilityLevel.PORTABLE,
        clients={"claude_code": ClientState.ENABLED, "codex": ClientState.ENABLED},
        ownership=OWNERSHIP,
    )


def provider_native_plugin() -> ExtensionRecord:
    return ExtensionRecord(
        capability_id="claude-only-plugin",
        extension_class=ExtensionClass.PLUGIN,
        title="A Claude marketplace plugin",
        deployment_mode=DeploymentMode.PROVIDER_NATIVE,
        portability=PortabilityLevel.PROVIDER_NATIVE,
        clients={"claude_code": ClientState.ENABLED},
        ownership=None,
    )


def unmanaged_discovery(name: str = "someones-own-skill") -> DiscoveredCapability:
    return DiscoveredCapability(
        client_id="claude_code",
        extension_class=ExtensionClass.SKILL,
        name=name,
        fingerprint="f" * 16,
        owner=None,
    )


class ExtensionRegistryContract:
    @pytest.fixture
    def registry(self) -> ExtensionRegistry:
        raise NotImplementedError("supply an ExtensionRegistry fixture")

    def test_register_then_get(self, registry: ExtensionRegistry) -> None:
        record = managed_skill()
        registry.register(record)
        assert registry.get("never4ga-startup") == record

    def test_get_unknown_returns_none(self, registry: ExtensionRegistry) -> None:
        assert registry.get("nothing") is None

    def test_register_replaces(self, registry: ExtensionRegistry) -> None:
        registry.register(managed_skill())
        registry.register(managed_skill())
        assert len(registry.list_records()) == 1

    def test_list_filters_by_class_and_mode(self, registry: ExtensionRegistry) -> None:
        registry.register(managed_skill())
        registry.register(provider_native_plugin())
        skills = registry.list_records(extension_class=ExtensionClass.SKILL)
        assert [r.capability_id for r in skills] == ["never4ga-startup"]
        native = registry.list_records(deployment_mode=DeploymentMode.PROVIDER_NATIVE)
        assert [r.capability_id for r in native] == ["claude-only-plugin"]

    # --- sync planning -----------------------------------------------------

    def test_a_managed_capability_is_installed_where_missing(
        self, registry: ExtensionRegistry
    ) -> None:
        registry.register(managed_skill())
        plan = registry.plan_sync("claude_code", discovered=())
        assert [a.kind for a in plan.actions] == [SyncActionKind.INSTALL]

    def test_unknown_configuration_is_preserved_never_claimed(
        self, registry: ExtensionRegistry
    ) -> None:
        # core/09 section 11.
        registry.register(managed_skill())
        plan = registry.plan_sync("claude_code", discovered=(unmanaged_discovery(),))
        preserved = [a for a in plan.actions if a.kind is SyncActionKind.PRESERVE]
        assert [a.name for a in preserved] == ["someones-own-skill"]
        assert registry.get("someones-own-skill") is None

    def test_a_plan_never_removes_anything_unmanaged(self, registry: ExtensionRegistry) -> None:
        registry.register(managed_skill())
        plan = registry.plan_sync(
            "claude_code",
            discovered=(unmanaged_discovery("a"), unmanaged_discovery("b")),
        )
        removals = [a for a in plan.actions if a.kind is SyncActionKind.REMOVE]
        assert removals == []

    def test_a_name_collision_with_unmanaged_config_is_a_conflict_not_an_overwrite(
        self, registry: ExtensionRegistry
    ) -> None:
        # core/09 section 11: detect conflicting names, refuse silent overwrite.
        registry.register(managed_skill())
        plan = registry.plan_sync(
            "claude_code", discovered=(unmanaged_discovery("never4ga-startup"),)
        )
        (action,) = [a for a in plan.actions if a.name == "never4ga-startup"]
        assert action.kind is SyncActionKind.CONFLICT
        assert plan.has_conflicts

    def test_an_owned_copy_that_drifted_is_updated(self, registry: ExtensionRegistry) -> None:
        registry.register(managed_skill())
        drifted = DiscoveredCapability(
            client_id="claude_code",
            extension_class=ExtensionClass.SKILL,
            name="never4ga-startup",
            fingerprint="stale",
            owner="never4ga",
        )
        plan = registry.plan_sync("claude_code", discovered=(drifted,))
        (action,) = [a for a in plan.actions if a.name == "never4ga-startup"]
        assert action.kind is SyncActionKind.UPDATE

    def test_an_owned_copy_in_sync_is_left_alone(self, registry: ExtensionRegistry) -> None:
        record = managed_skill()
        registry.register(record)
        current = DiscoveredCapability(
            client_id="claude_code",
            extension_class=ExtensionClass.SKILL,
            name="never4ga-startup",
            fingerprint=record.ownership.content_hash if record.ownership else "",
            owner="never4ga",
        )
        plan = registry.plan_sync("claude_code", discovered=(current,))
        (action,) = [a for a in plan.actions if a.name == "never4ga-startup"]
        assert action.kind is SyncActionKind.NOOP

    def test_provider_native_is_never_pushed_to_another_client(
        self, registry: ExtensionRegistry
    ) -> None:
        # core/09 section 23: a Claude-only plugin may be registered without
        # Never4gA attempting to install it in Codex.
        registry.register(provider_native_plugin())
        plan = registry.plan_sync("codex", discovered=())
        assert [a.kind for a in plan.actions] == []

    def test_provider_native_is_not_installed_even_on_its_own_client(
        self, registry: ExtensionRegistry
    ) -> None:
        registry.register(provider_native_plugin())
        plan = registry.plan_sync("claude_code", discovered=())
        assert [a.kind for a in plan.actions] == [SyncActionKind.SKIP]

    def test_a_disabled_client_gets_no_install(self, registry: ExtensionRegistry) -> None:
        record = ExtensionRecord(
            capability_id="never4ga-startup",
            extension_class=ExtensionClass.SKILL,
            title="Never4gA Startup",
            deployment_mode=DeploymentMode.MANAGED,
            portability=PortabilityLevel.PORTABLE,
            clients={"claude_code": ClientState.DISABLED},
            ownership=OWNERSHIP,
        )
        registry.register(record)
        plan = registry.plan_sync("claude_code", discovered=())
        assert [a.kind for a in plan.actions] == []

    def test_a_disabled_client_with_an_owned_copy_may_be_cleaned_up(
        self, registry: ExtensionRegistry
    ) -> None:
        registry.register(
            ExtensionRecord(
                capability_id="never4ga-startup",
                extension_class=ExtensionClass.SKILL,
                title="Never4gA Startup",
                deployment_mode=DeploymentMode.MANAGED,
                portability=PortabilityLevel.PORTABLE,
                clients={"claude_code": ClientState.DISABLED},
                ownership=OWNERSHIP,
            )
        )
        owned = DiscoveredCapability(
            client_id="claude_code",
            extension_class=ExtensionClass.SKILL,
            name="never4ga-startup",
            fingerprint="whatever",
            owner="never4ga",
        )
        plan = registry.plan_sync("claude_code", discovered=(owned,))
        (action,) = [a for a in plan.actions if a.name == "never4ga-startup"]
        assert action.kind is SyncActionKind.REMOVE

    def test_removing_a_client_does_not_alter_canonical_records(
        self, registry: ExtensionRegistry
    ) -> None:
        # core/09 section 23 "Client removed".
        registry.register(managed_skill())
        before = registry.get("never4ga-startup")
        registry.plan_sync("a-client-that-vanished", discovered=())
        assert registry.get("never4ga-startup") == before

    def test_planning_is_side_effect_free(self, registry: ExtensionRegistry) -> None:
        registry.register(managed_skill())
        first = registry.plan_sync("claude_code", discovered=(unmanaged_discovery(),))
        second = registry.plan_sync("claude_code", discovered=(unmanaged_discovery(),))
        assert [(a.kind, a.name) for a in first.actions] == [
            (a.kind, a.name) for a in second.actions
        ]

    def test_every_action_explains_itself(self, registry: ExtensionRegistry) -> None:
        registry.register(managed_skill())
        registry.register(provider_native_plugin())
        plan = registry.plan_sync("claude_code", discovered=(unmanaged_discovery(),))
        assert plan.actions
        for action in plan.actions:
            assert action.reason

    def test_higher_risk_capabilities_are_surfaced_in_the_plan(
        self, registry: ExtensionRegistry
    ) -> None:
        registry.register(
            ExtensionRecord(
                capability_id="openproject-mcp",
                extension_class=ExtensionClass.MCP_SERVER,
                title="OpenProject MCP",
                deployment_mode=DeploymentMode.MANAGED,
                portability=PortabilityLevel.ADAPTABLE,
                clients={"claude_code": ClientState.ENABLED},
                ownership=OWNERSHIP,
                permissions=Permissions(
                    read_only=False, writes_external_state=True, requires_credentials=True
                ),
            )
        )
        plan = registry.plan_sync("claude_code", discovered=())
        (action,) = plan.actions
        assert action.requires_review
