"""Extension registry domain model.

core/09. The registry is a personal governance layer, not a marketplace
(section 21). Its job is to make two mistakes impossible:

1. overwriting or removing configuration Never4gA does not own (section 11);
2. presenting a provider-native capability as portable (section 15).

Both are encoded as validated states rather than as conventions, so a mistake
is a construction-time error rather than a destructive sync.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from never4ga.errors import ExtensionOwnershipError

__all__ = [
    "ClientState",
    "DeploymentMode",
    "DiscoveredCapability",
    "ExtensionClass",
    "ExtensionRecord",
    "OwnershipProof",
    "Permissions",
    "PortabilityLevel",
    "RiskLevel",
    "SyncAction",
    "SyncActionKind",
    "SyncPlan",
    "plan_capability_sync",
]

#: The owner string Never4gA stamps into configuration it generates.
NEVER4GA_OWNER = "never4ga"


class ExtensionClass(enum.StrEnum):
    """The classes of core/09 section 3."""

    SKILL = "skill"
    MCP_SERVER = "mcp_server"
    PLUGIN = "plugin"
    HOOK = "hook"
    RULE = "rule"
    SUBAGENT = "subagent"
    COMMAND = "command"
    MEMORY_PROVIDER = "memory_provider"
    WORK_MANAGEMENT_PROVIDER = "work_management_provider"
    STORAGE_BACKEND = "storage_backend"
    CONTEXT_SIGNAL_PROVIDER = "context_signal_provider"


class DeploymentMode(enum.StrEnum):
    """Who owns the target configuration (core/09 section 6)."""

    MANAGED = "managed"
    ADOPTED = "adopted"
    REGISTERED = "registered"
    PROVIDER_NATIVE = "provider_native"


class PortabilityLevel(enum.StrEnum):
    """How far a capability travels between tools (core/09 section 19)."""

    PORTABLE = "portable"
    ADAPTABLE = "adaptable"
    PROVIDER_NATIVE = "provider_native"
    UNKNOWN = "unknown"


class ClientState(enum.StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class RiskLevel(enum.StrEnum):
    LOW = "low"
    ELEVATED = "elevated"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class Permissions:
    """What a capability can reach (core/09 section 14).

    Credentials themselves are never here -- only whether one is required. The
    value lives in the SecretStore (core/05 section 9).
    """

    read_only: bool = True
    writes_external_state: bool = False
    filesystem_access: bool = False
    network_access: bool = False
    executes_commands: bool = False
    requires_credentials: bool = False

    @property
    def risk_level(self) -> RiskLevel:
        if self.executes_commands:
            return RiskLevel.HIGH
        if not self.read_only or self.writes_external_state or self.filesystem_access:
            return RiskLevel.ELEVATED
        return RiskLevel.LOW


@dataclass(frozen=True, slots=True)
class OwnershipProof:
    """Evidence that Never4gA generated a piece of configuration.

    Without proof, sync may not remove or overwrite anything. ``generated_at``
    is optional and supplied by the caller: a generation timestamp is never
    fabricated (core/02, ``generated.at``).
    """

    owner: str
    content_hash: str
    generated_at: str | None = None

    def __post_init__(self) -> None:
        if not self.owner.strip():
            raise ExtensionOwnershipError("ownership proof requires an owner")
        if not self.content_hash.strip():
            raise ExtensionOwnershipError("ownership proof requires a content hash")


@dataclass(frozen=True, slots=True)
class ExtensionRecord:
    """A canonical registry entry (core/09 section 5)."""

    capability_id: str
    extension_class: ExtensionClass
    title: str
    deployment_mode: DeploymentMode
    portability: PortabilityLevel
    clients: Mapping[str, ClientState] = field(default_factory=dict)
    permissions: Permissions = field(default_factory=Permissions)
    ownership: OwnershipProof | None = None
    origin: str = ""
    #: When a person adopted this, and what was there before they did
    #: (core/09 section 13: origin, review date, hash). Empty on anything
    #: that was never adopted. The hash is of the *replaced* content, so an
    #: adoption stays auditable against the repository's own history after
    #: the file it replaced is gone; ``ownership`` hashes what is there now.
    adopted_at: str = ""
    replaced_hash: str = ""

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id is required")
        object.__setattr__(self, "clients", MappingProxyType(dict(self.clients)))
        self._validate_ownership()
        self._validate_portability()

    def _validate_ownership(self) -> None:
        owned = self.deployment_mode in (DeploymentMode.MANAGED, DeploymentMode.ADOPTED)
        if owned and self.ownership is None:
            raise ExtensionOwnershipError(
                f"{self.deployment_mode.value!r} capability {self.capability_id!r} "
                "requires an ownership proof"
            )
        if not owned and self.ownership is not None:
            raise ExtensionOwnershipError(
                f"{self.deployment_mode.value!r} capability {self.capability_id!r} "
                "must not claim ownership of configuration Never4gA does not own"
            )

    def _validate_portability(self) -> None:
        if (
            self.deployment_mode is DeploymentMode.PROVIDER_NATIVE
            and self.portability is not PortabilityLevel.PROVIDER_NATIVE
        ):
            raise ExtensionOwnershipError(
                f"{self.capability_id!r} is provider-native and cannot declare "
                f"portability {self.portability.value!r}"
            )

    @property
    def is_owned_by_never4ga(self) -> bool:
        return self.deployment_mode in (DeploymentMode.MANAGED, DeploymentMode.ADOPTED)

    @property
    def may_be_removed_by_sync(self) -> bool:
        """Only proven-managed configuration may be removed (core/09 section 11)."""
        return self.deployment_mode is DeploymentMode.MANAGED and self.ownership is not None

    @property
    def is_cross_client_syncable(self) -> bool:
        return self.portability in (PortabilityLevel.PORTABLE, PortabilityLevel.ADAPTABLE)

    def is_enabled_for(self, client_id: str) -> bool:
        return self.clients.get(client_id) is ClientState.ENABLED


@dataclass(frozen=True, slots=True)
class DiscoveredCapability:
    """Something an adapter found installed in a client.

    core/09 section 12: discovery output is local derived state and does not
    automatically become canonical. ``owner`` is whatever ownership marker the
    adapter found -- ``None`` means "not ours".
    """

    client_id: str
    extension_class: ExtensionClass
    name: str
    fingerprint: str = ""
    owner: str | None = None
    source_path: str | None = None

    @property
    def is_never4ga_owned(self) -> bool:
        return self.owner == NEVER4GA_OWNER


class SyncActionKind(enum.StrEnum):
    INSTALL = "install"
    UPDATE = "update"
    REMOVE = "remove"
    NOOP = "noop"
    #: Present in the client, not ours, left exactly as it is.
    PRESERVE = "preserve"
    #: Ours, but not eligible for cross-client sync (provider-native, unknown).
    SKIP = "skip"
    #: A managed name clashes with unmanaged configuration; a human decides.
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class SyncAction:
    kind: SyncActionKind
    client_id: str
    name: str
    reason: str
    capability_id: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW

    @property
    def is_destructive(self) -> bool:
        return self.kind in (SyncActionKind.REMOVE, SyncActionKind.UPDATE)

    @property
    def requires_review(self) -> bool:
        """A conflict, or an elevated-risk change, must be shown before applying."""
        return self.kind is SyncActionKind.CONFLICT or self.risk_level is not RiskLevel.LOW


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """A diffable, side-effect-free description of what a sync would do."""

    client_id: str
    actions: tuple[SyncAction, ...] = ()

    @property
    def has_conflicts(self) -> bool:
        return any(action.kind is SyncActionKind.CONFLICT for action in self.actions)

    @property
    def requires_review(self) -> bool:
        return any(action.requires_review for action in self.actions)

    @property
    def removals(self) -> tuple[SyncAction, ...]:
        return tuple(a for a in self.actions if a.kind is SyncActionKind.REMOVE)


def plan_capability_sync(
    client_id: str,
    records: Sequence[ExtensionRecord],
    discovered: Sequence[DiscoveredCapability],
) -> SyncPlan:
    """What a sync would do, as a pure function of what is registered and found.

    Every rule in `core/09` section 11 and `core/04` section 25 lives here, and
    nowhere else. It is a domain function rather than part of a registry
    adapter, because the sync engine needs the same rules and may not import
    an adapter to get them.

    Conservative by construction. Anything Never4gA cannot prove it owns is
    preserved untouched, and a name clash becomes a conflict for a human rather
    than an overwrite.
    """
    found = {item.name: item for item in discovered if item.client_id == client_id}
    actions: list[SyncAction] = []
    planned: set[str] = set()

    for record in sorted(records, key=lambda item: item.capability_id):
        action = _plan_one(client_id, record, found.get(record.capability_id))
        if action is not None:
            actions.append(action)
            planned.add(record.capability_id)

    # Everything else installed in the client stays exactly as it is.
    # core/09 section 11: sync never deletes an unmanaged capability.
    actions.extend(
        SyncAction(
            kind=SyncActionKind.PRESERVE,
            client_id=client_id,
            name=name,
            reason="unmanaged_capability_preserved",
        )
        for name, item in sorted(found.items())
        if name not in planned and not item.is_never4ga_owned
    )
    return SyncPlan(client_id=client_id, actions=tuple(actions))


def _plan_one(
    client_id: str,
    record: ExtensionRecord,
    installed: DiscoveredCapability | None,
) -> SyncAction | None:
    risk = record.permissions.risk_level

    def action(kind: SyncActionKind, reason: str) -> SyncAction:
        return SyncAction(
            kind=kind,
            client_id=client_id,
            name=record.capability_id,
            reason=reason,
            capability_id=record.capability_id,
            risk_level=risk,
        )

    if not record.is_cross_client_syncable:
        # Provider-native and unreviewed capabilities are recorded, never
        # deployed (core/09 sections 15 and 19). They are only mentioned for
        # the client that actually declares them.
        if record.is_enabled_for(client_id):
            return action(SyncActionKind.SKIP, f"portability_{record.portability.value}")
        return None

    if not record.is_enabled_for(client_id):
        if installed is not None and installed.is_never4ga_owned:
            if not record.may_be_removed_by_sync:
                return action(SyncActionKind.CONFLICT, "removal_without_ownership_proof")
            return action(SyncActionKind.REMOVE, "disabled_for_client")
        return None

    if installed is None:
        return action(SyncActionKind.INSTALL, "missing_in_client")

    if not installed.is_never4ga_owned:
        return action(SyncActionKind.CONFLICT, "name_collision_with_unmanaged_capability")

    expected = record.ownership.content_hash if record.ownership else ""
    if installed.fingerprint != expected:
        return action(SyncActionKind.UPDATE, "client_copy_drifted")
    return action(SyncActionKind.NOOP, "client_copy_current")
