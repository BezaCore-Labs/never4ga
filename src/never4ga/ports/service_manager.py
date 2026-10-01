"""ServiceManager -- reporting and controlling the background service.

core/05 section 20 lists this port; section 11 names ``systemd --user`` as the
first platform adapter and adds the constraint that matters: "Core
functionality cannot depend semantically on systemd." Everything above this
port asks whether the service is running, not what an init system thinks.

There is no ``install``, deliberately: writing a unit file means writing into
another tool's directory, which section 7 of details/security-configuration.md
does not permit. The unit is documented and the user installs it.

A platform with no service manager is a normal answer, not a failure. Such an
implementation reports :attr:`ServiceState.UNSUPPORTED` and refuses control,
rather than returning a status nobody can act on (core/05 section 19).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

__all__ = ["ServiceManager", "ServiceState", "ServiceStatus"]


class ServiceState(StrEnum):
    """What the platform says about the service.

    The transitional states are kept distinct from the settled ones because a
    caller that treats "starting" as "stopped" restarts a service that was
    already on its way up.
    """

    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"

    #: The platform knows how to manage services; this one is not registered.
    NOT_INSTALLED = "not_installed"

    #: There is no service manager here at all.
    UNSUPPORTED = "unsupported"

    #: The platform answered something this adapter does not recognise. Said
    #: plainly, rather than guessed at.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """One reading of the service's state.

    ``detail`` is human-readable text for the CLI to print. It is assembled by
    the adapter from state the platform reports, and never from anything that
    could carry a credential (details/security-configuration.md section 9).
    """

    state: ServiceState
    detail: str = ""

    @property
    def is_running(self) -> bool:
        return self.state is ServiceState.RUNNING


@runtime_checkable
class ServiceManager(Protocol):
    @property
    def can_control(self) -> bool:
        """Whether ``start``, ``stop`` and ``restart`` can do anything here."""
        ...

    def status(self) -> ServiceStatus:
        """Report. Never raises: a diagnostic that fails is a second failure."""
        ...

    def start(self) -> ServiceStatus: ...

    def stop(self) -> ServiceStatus: ...

    def restart(self) -> ServiceStatus: ...
