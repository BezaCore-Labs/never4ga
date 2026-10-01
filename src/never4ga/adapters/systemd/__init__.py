"""systemd adapter -- the Linux user-service manager (core/05 section 11).

The only package that knows what ``systemctl`` is. Everything above it works
through :class:`never4ga.ports.service_manager.ServiceManager`, so a machine
with no systemd loses service *control* and nothing else.
"""

from __future__ import annotations

from never4ga.adapters.systemd.service_manager import (
    DEFAULT_UNIT,
    CommandResult,
    CommandRunner,
    SystemdUserServiceManager,
)

__all__ = [
    "DEFAULT_UNIT",
    "CommandResult",
    "CommandRunner",
    "SystemdUserServiceManager",
]
