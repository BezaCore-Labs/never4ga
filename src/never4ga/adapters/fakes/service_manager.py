"""In-memory ServiceManager.

It models a service that starts and stops, so that callers -- the CLI's
`service` verbs above all -- can be tested against every state including
``failed``, which a live systemd will not produce on demand.
"""

from __future__ import annotations

from never4ga.ports.service_manager import ServiceState, ServiceStatus

__all__ = ["FakeServiceManager"]


class FakeServiceManager:
    def __init__(self, *, state: ServiceState = ServiceState.STOPPED) -> None:
        self._state = state
        self.commands: list[str] = []

    @property
    def can_control(self) -> bool:
        return True

    def status(self) -> ServiceStatus:
        return ServiceStatus(self._state, f"the fake service is {self._state}")

    def start(self) -> ServiceStatus:
        return self._transition("start", ServiceState.RUNNING)

    def stop(self) -> ServiceStatus:
        return self._transition("stop", ServiceState.STOPPED)

    def restart(self) -> ServiceStatus:
        return self._transition("restart", ServiceState.RUNNING)

    def _transition(self, command: str, state: ServiceState) -> ServiceStatus:
        self.commands.append(command)
        self._state = state
        return self.status()
