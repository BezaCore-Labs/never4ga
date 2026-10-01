"""Extension record states (core/09 sections 2, 6, 14, 19).

- The registry distinguishes managed from registered/provider-native.
- Portable and provider-native capability are distinct types/states.
"""

from __future__ import annotations

import pytest

from never4ga.domain.extensions import (
    ClientState,
    DeploymentMode,
    ExtensionClass,
    ExtensionRecord,
    OwnershipProof,
    Permissions,
    PortabilityLevel,
    RiskLevel,
)
from never4ga.errors import ExtensionOwnershipError

OWNERSHIP = OwnershipProof(owner="never4ga", content_hash="a" * 64)


def make_record(**overrides: object) -> ExtensionRecord:
    defaults: dict[str, object] = {
        "capability_id": "never4ga-startup",
        "extension_class": ExtensionClass.SKILL,
        "title": "Never4gA Startup",
        "deployment_mode": DeploymentMode.MANAGED,
        "portability": PortabilityLevel.PORTABLE,
        "clients": {"claude_code": ClientState.ENABLED},
        "ownership": OWNERSHIP,
    }
    defaults.update(overrides)
    return ExtensionRecord(**defaults)  # type: ignore[arg-type]


class TestDeploymentModes:
    def test_the_four_modes_of_core_09_section_6_exist(self) -> None:
        assert {mode.value for mode in DeploymentMode} == {
            "managed",
            "adopted",
            "registered",
            "provider_native",
        }

    def test_managed_is_owned_by_never4ga(self) -> None:
        assert make_record().is_owned_by_never4ga

    def test_adopted_is_owned_by_never4ga(self) -> None:
        record = make_record(deployment_mode=DeploymentMode.ADOPTED)
        assert record.is_owned_by_never4ga

    def test_registered_is_known_but_not_owned(self) -> None:
        record = make_record(
            deployment_mode=DeploymentMode.REGISTERED,
            portability=PortabilityLevel.UNKNOWN,
            ownership=None,
        )
        assert not record.is_owned_by_never4ga
        assert not record.may_be_removed_by_sync

    def test_provider_native_is_neither_owned_nor_synced(self) -> None:
        record = make_record(
            deployment_mode=DeploymentMode.PROVIDER_NATIVE,
            portability=PortabilityLevel.PROVIDER_NATIVE,
            ownership=None,
        )
        assert not record.is_owned_by_never4ga
        assert not record.is_cross_client_syncable

    def test_only_managed_with_proof_may_be_removed(self) -> None:
        assert make_record().may_be_removed_by_sync
        assert not make_record(deployment_mode=DeploymentMode.ADOPTED).may_be_removed_by_sync


class TestOwnershipValidation:
    def test_managed_requires_ownership_proof(self) -> None:
        # core/09 section 11: remove only entries proven to be Never4gA-managed.
        with pytest.raises(ExtensionOwnershipError, match="ownership"):
            make_record(ownership=None)

    def test_registered_must_not_claim_ownership(self) -> None:
        with pytest.raises(ExtensionOwnershipError, match="ownership"):
            make_record(
                deployment_mode=DeploymentMode.REGISTERED,
                portability=PortabilityLevel.UNKNOWN,
                ownership=OWNERSHIP,
            )

    def test_provider_native_must_not_claim_ownership(self) -> None:
        with pytest.raises(ExtensionOwnershipError, match="ownership"):
            make_record(
                deployment_mode=DeploymentMode.PROVIDER_NATIVE,
                portability=PortabilityLevel.PROVIDER_NATIVE,
                ownership=OWNERSHIP,
            )


class TestPortabilityIsDistinctFromDeployment:
    def test_the_four_levels_of_core_09_section_19_exist(self) -> None:
        assert {level.value for level in PortabilityLevel} == {
            "portable",
            "adaptable",
            "provider_native",
            "unknown",
        }

    def test_provider_native_deployment_cannot_be_declared_portable(self) -> None:
        # core/09 section 15: provider-native functionality must never be
        # presented as portable.
        with pytest.raises(ExtensionOwnershipError, match="portab"):
            make_record(
                deployment_mode=DeploymentMode.PROVIDER_NATIVE,
                portability=PortabilityLevel.PORTABLE,
                ownership=None,
            )

    def test_portable_and_adaptable_are_syncable_provider_native_is_not(self) -> None:
        assert make_record().is_cross_client_syncable
        assert make_record(portability=PortabilityLevel.ADAPTABLE).is_cross_client_syncable
        assert not make_record(
            deployment_mode=DeploymentMode.PROVIDER_NATIVE,
            portability=PortabilityLevel.PROVIDER_NATIVE,
            ownership=None,
        ).is_cross_client_syncable

    def test_unknown_portability_is_not_synced_until_reviewed(self) -> None:
        record = make_record(
            deployment_mode=DeploymentMode.REGISTERED,
            portability=PortabilityLevel.UNKNOWN,
            ownership=None,
        )
        assert not record.is_cross_client_syncable


class TestPermissionsAndRisk:
    def test_a_read_only_capability_is_low_risk(self) -> None:
        assert Permissions().risk_level is RiskLevel.LOW

    def test_a_write_capable_server_is_flagged_higher_risk(self) -> None:
        # core/09 sections 14 and 23: identify write capability before
        # cross-client enablement.
        permissions = Permissions(read_only=False, writes_external_state=True)
        assert permissions.risk_level is RiskLevel.ELEVATED

    def test_command_execution_is_the_highest_risk(self) -> None:
        permissions = Permissions(read_only=False, executes_commands=True)
        assert permissions.risk_level is RiskLevel.HIGH

    def test_records_carry_their_permissions(self) -> None:
        record = make_record(
            extension_class=ExtensionClass.MCP_SERVER,
            permissions=Permissions(read_only=False, writes_external_state=True),
        )
        assert record.permissions.risk_level is RiskLevel.ELEVATED

    def test_no_secret_value_can_be_attached_to_a_record(self) -> None:
        # core/09 section 5: no credentials appear in a registry record.
        assert not hasattr(make_record(), "secret")
        assert not hasattr(make_record(), "token")


class TestClientEnablement:
    def test_a_record_reports_its_enabled_clients(self) -> None:
        record = make_record(
            clients={"claude_code": ClientState.ENABLED, "codex": ClientState.DISABLED}
        )
        assert record.is_enabled_for("claude_code")
        assert not record.is_enabled_for("codex")

    def test_an_unknown_client_is_not_enabled(self) -> None:
        assert not make_record().is_enabled_for("antigravity_cli")

    def test_clients_mapping_is_read_only(self) -> None:
        record = make_record()
        with pytest.raises(TypeError):
            record.clients["codex"] = ClientState.ENABLED  # type: ignore[index]
