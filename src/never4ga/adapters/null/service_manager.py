"""NullServiceManager -- a platform with no service manager.

core/05 section 11 makes macOS and Windows later extensions, so "there is no
way to manage a background service here" is a state Never4gA has to represent
today. It reports :attr:`ServiceState.UNSUPPORTED` so that
``never4ga service status`` still answers, and refuses control rather than
returning a status that claims something happened.

The service itself still runs: ``never4ga serve`` in the foreground needs no
service manager at all.
"""

from __future__ import annotations

from never4ga.errors import ServiceControlError
from never4ga.ports.service_manager import ServiceState, ServiceStatus

__all__ = ["NullServiceManager"]

_DETAIL = (
    "no service manager is configured for this platform; run `never4ga serve` in the foreground"
)


class NullServiceManager:
    @property
    def can_control(self) -> bool:
        return False

    def status(self) -> ServiceStatus:
        return ServiceStatus(ServiceState.UNSUPPORTED, _DETAIL)

    def start(self) -> ServiceStatus:
        raise ServiceControlError(f"cannot start the service: {_DETAIL}")

    def stop(self) -> ServiceStatus:
        raise ServiceControlError(f"cannot stop the service: {_DETAIL}")

    def restart(self) -> ServiceStatus:
        raise ServiceControlError(f"cannot restart the service: {_DETAIL}")
