"""Machine-local configuration (core/05 section 9).

core/05 separates three kinds of configuration. Canonical configuration is
Markdown in the vault. Secrets live in a secret store. What is left describes
*this machine* -- where the vault is, which port the local service answers on --
and belongs outside the vault, because it does not travel with it.

The format is TOML at ``<config>/never4ga/config.toml``, read with the standard
library. Never4gA reads this file and never writes it: it is the user's, and a
tool that rewrites a hand-edited config loses comments and ordering the first
time it does so.

This module is a leaf, like :mod:`never4ga.platform_paths`. It is deliberately
not a port: there is one format, one direction, and no second implementation to
abstract over. What it does own is the loopback rule -- :class:`ServiceEndpoint`
refuses a host that is not loopback, so no downstream caller can bind the
network by accident (core/05 section 12,
details/security-configuration.md section 2).
"""

from __future__ import annotations

import ipaddress
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from never4ga.domain.configuration import RetiredSetting
from never4ga.errors import ConfigurationError, Never4gaError
from never4ga.platform_paths import PlatformPaths

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_PORT",
    "RETIRED_SETTINGS",
    "VAULT_ENVIRONMENT_VARIABLE",
    "LocalConfig",
    "MaintenanceSettings",
    "ServiceEndpoint",
    "default_vault_root",
]

#: Top-level keys Never4gA used to read and no longer does, and why.
#:
#: A known list, never "anything unrecognised". An unknown key is tolerated on
#: purpose, so a config written by a later version does not make this one
#: complain; only a key this build knows it has retired earns a word from
#: `doctor`. A retired block that still looks live, such as an embedding host,
#: misleads anyone diagnosing the system, so it is worth reporting.
RETIRED_SETTINGS: Final[dict[str, str]] = {
    "embedding": (
        "the vector lane is retired and nothing reads this table; a host "
        "here looks live but is ignored, which misleads anyone diagnosing "
        "search. Delete the block"
    ),
}

#: Where the vault is, when no interface was told explicitly. It lives here
#: rather than in `cli` because MCP resolves a vault the same three ways and may
#: not import another composition root. Two interfaces must never disagree about
#: which vault they serve.
VAULT_ENVIRONMENT_VARIABLE: Final = "NEVER4GA_VAULT"

#: The default loopback port. The number is arbitrary and configurable; what is
#: deliberate is that it sits below the Linux default ephemeral range
#: (32768-60999), so a transient outgoing connection can never already hold the
#: port the service means to bind.
DEFAULT_PORT: Final = 7377

CONFIG_FILENAME: Final = "config.toml"

#: Hostnames that resolve to the loopback interface without a DNS lookup.
_LOOPBACK_NAMES: Final = frozenset({"localhost"})

_MAX_PORT: Final = 65535


@dataclass(frozen=True, slots=True)
class ServiceEndpoint:
    """Where the local service listens.

    Loopback only. core/05 section 12 says the service MUST NOT bind
    ``0.0.0.0``, and details/security-configuration.md section 2 adds no LAN
    bind, no public bind, and remote exposure only as explicit future
    configuration. The check lives in ``__post_init__`` rather than in the
    parser so that every construction is covered, not only the one that reads a
    file.
    """

    host: str = "127.0.0.1"

    #: Port 0 asks the operating system for a free port. The service then
    #: reports which one it was given, which is how a test, or a user who does
    #: not want to pick a number, avoids colliding with something already
    #: listening.
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        if not _is_loopback(self.host):
            raise ConfigurationError(
                f"service host {self.host!r} is not loopback; "
                "core/05 section 12 binds 127.0.0.1 only, and LAN or public "
                "exposure is a future explicit configuration rather than a "
                "setting that already works"
            )
        if not 0 <= self.port <= _MAX_PORT:
            raise ConfigurationError(f"service port {self.port} is outside 0-{_MAX_PORT}")

    @property
    def base_url(self) -> str:
        """The URL a local client uses to reach the service."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


@dataclass(frozen=True, slots=True)
class MaintenanceSettings:
    """How often the service runs each maintenance tier.

    `details/data-indexing-maintenance.md` section 22 divides maintenance into
    immediate, frequent-lightweight and periodic-heavier work, and makes the
    cadence configurable rather than hardcoded. It runs in-process on the
    service, which already owns the index and watches the vault.

    The immediate tier has no cadence: it runs when the watcher's batch
    settles.

    **Absent means the defaults, and the defaults are on**, because
    maintenance is the service doing the job it was started for.
    ``enabled = false`` is for somebody who wants a running service to stop
    doing it, without stopping the service.
    """

    enabled: bool = True
    #: The frequent tier. Minutes rather than seconds: it is a sweep, not a
    #: response to a save, and the save is already covered by the immediate
    #: tier.
    frequent: float = 300.0
    #: The periodic tier, which carries the full diagnosis. It is affordable
    #: hourly but not per save, which is why there is more than one tier.
    periodic: float = 3600.0


@dataclass(frozen=True, slots=True)
class LocalConfig:
    """The machine-local settings read from ``config.toml``.

    Repo-to-workspace mappings, connection registries and adapter deployment
    state are also machine-local (core/05 section 9), but live in their own
    files. An unknown key is tolerated rather than refused, so a config
    written by a later version does not stop this one starting.
    """

    vault: Path | None = None
    service: ServiceEndpoint = field(default_factory=ServiceEndpoint)
    maintenance: MaintenanceSettings = field(default_factory=MaintenanceSettings)
    #: Keys found in the file that no longer do anything. Carried rather
    #: than acted on: Never4gA reads this file and never writes it, so the most
    #: it can do is hand the fact to `doctor` and let a person delete the block.
    retired: tuple[RetiredSetting, ...] = ()

    @classmethod
    def default_path(cls, paths: PlatformPaths | None = None) -> Path:
        resolved = PlatformPaths.resolve() if paths is None else paths
        return resolved.config / CONFIG_FILENAME

    @classmethod
    def load(cls, path: Path | None = None) -> LocalConfig:
        """Read the config file, or return defaults when there is none.

        An absent file is the normal case and never an error: the CLI and the
        service must both work on a machine where nobody has configured
        anything.
        """
        location = cls.default_path() if path is None else path
        try:
            text = location.read_text()
        except FileNotFoundError:
            return cls()
        except OSError as error:
            raise ConfigurationError(f"cannot read config at {location}: {error}") from error
        return cls.parse(text, source=location)

    @classmethod
    def parse(cls, text: str, source: Path | None = None) -> LocalConfig:
        where = f" at {source}" if source is not None else ""
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise ConfigurationError(f"config{where} is not valid TOML: {error}") from error
        return cls(
            vault=_vault_path(document.get("vault"), where),
            service=_endpoint(document.get("service", {}), where),
            maintenance=_maintenance(document.get("maintenance", {}), where),
            retired=tuple(
                RetiredSetting(key, reason)
                for key, reason in RETIRED_SETTINGS.items()
                if key in document
            ),
        )


def _maintenance(section: Any, where: str) -> MaintenanceSettings:
    if not isinstance(section, dict):
        raise ConfigurationError(f"config{where}: [maintenance] must be a table")
    defaults = MaintenanceSettings()

    enabled = section.get("enabled", defaults.enabled)
    if not isinstance(enabled, bool):
        raise ConfigurationError(
            f"config{where}: [maintenance] enabled must be true or false, got {enabled!r}"
        )
    return MaintenanceSettings(
        enabled=enabled,
        frequent=_cadence(section, "frequent", defaults.frequent, where),
        periodic=_cadence(section, "periodic", defaults.periodic, where),
    )


def _cadence(section: dict[str, Any], key: str, default: float, where: str) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigurationError(
            f"config{where}: [maintenance] {key} must be a positive number of "
            f"seconds, got {value!r}"
        )
    return float(value)


def _vault_path(value: Any, where: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"config{where}: vault must be a path, not {type(value).__name__}")
    return Path(value).expanduser()


def _endpoint(section: Any, where: str) -> ServiceEndpoint:
    if not isinstance(section, dict):
        raise ConfigurationError(f"config{where}: [service] must be a table")
    defaults = ServiceEndpoint()
    host = section.get("host", defaults.host)
    if not isinstance(host, str):
        raise ConfigurationError(f"config{where}: service host must be a string")
    port = section.get("port", defaults.port)
    if not isinstance(port, int) or isinstance(port, bool):
        raise ConfigurationError(f"config{where}: service port must be an integer")
    return ServiceEndpoint(host=host, port=port)


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A name Never4gA cannot resolve without DNS. Refusing is the safe
        # answer: a hostname that happens to point at the loopback interface
        # today can point elsewhere tomorrow.
        return False


def retired_settings() -> tuple[RetiredSetting, ...]:
    """What this machine's config sets that no longer does anything.

    The one call every composition root makes, so `doctor` answers the same
    question whichever interface asked it. A config that cannot be read
    reports nothing rather than raising, for the reason
    :func:`default_vault_root` ignores one: a broken file is already its own
    finding, and a diagnosis that died reading it would report neither.
    """
    try:
        return LocalConfig.load().retired
    except Never4gaError:
        return ()


def default_vault_root() -> str:
    """Where a vault is, when nobody said: the environment, then config, then here.

    A config that cannot be read must not stop an interface resolving a vault
    the other two ways, so a broken file is ignored and `doctor` reports it.
    """
    from_environment = os.environ.get(VAULT_ENVIRONMENT_VARIABLE)
    if from_environment:
        return from_environment
    try:
        configured = LocalConfig.load().vault
    except Never4gaError:
        configured = None
    return str(configured) if configured is not None else str(Path.cwd())
