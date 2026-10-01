"""Run the ServiceManager contract against every implementation.

The systemd adapter is tested against a fake ``systemctl`` rather than a live
one. That is not a mock standing in for behaviour nobody checked: what the
adapter actually owns is the command it issues and the output it parses, and
both are exercised here. Requiring a running user manager would make the suite
untestable in CI and on any machine without systemd -- which is precisely the
machine the null implementation exists for.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from never4ga.adapters.fakes import FakeServiceManager
from never4ga.adapters.null import NullServiceManager
from never4ga.adapters.systemd import CommandResult, SystemdUserServiceManager
from never4ga.errors import ServiceControlError
from never4ga.ports.service_manager import ServiceManager, ServiceState
from tests.contracts.service_manager_contract import (
    ControllableServiceManagerContract,
    UnsupportedServiceManagerContract,
)

pytestmark = pytest.mark.contract

UNIT = "never4ga.service"


class FakeSystemctl:
    """Enough of ``systemctl --user`` to answer the adapter.

    It records every command line, so a test can assert what was asked for as
    well as what was done with the answer.
    """

    def __init__(self, *, load_state: str = "loaded", active_state: str = "inactive") -> None:
        self.load_state = load_state
        self.active_state = active_state
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> CommandResult:
        self.commands.append(tuple(command))
        verb = command[2] if len(command) > 2 else ""
        if verb == "show":
            return CommandResult(0, self._show())
        if verb == "start":
            self.active_state = "active"
        elif verb == "stop":
            self.active_state = "inactive"
        elif verb == "restart":
            self.active_state = "active"
        return CommandResult(0, "")

    def _show(self) -> str:
        sub = "running" if self.active_state == "active" else "dead"
        return f"LoadState={self.load_state}\nActiveState={self.active_state}\nSubState={sub}\n"


def _missing_systemctl(command: Sequence[str]) -> CommandResult:
    raise FileNotFoundError(command[0])


class TestFakeServiceManager(ControllableServiceManagerContract):
    @pytest.fixture
    def manager(self) -> ServiceManager:
        return FakeServiceManager()


class TestSystemdUserServiceManager(ControllableServiceManagerContract):
    @pytest.fixture
    def manager(self) -> ServiceManager:
        return SystemdUserServiceManager(UNIT, run=FakeSystemctl())


class TestNullServiceManager(UnsupportedServiceManagerContract):
    @pytest.fixture
    def manager(self) -> ServiceManager:
        return NullServiceManager()


class TestSystemdWithoutSystemd(UnsupportedServiceManagerContract):
    """A machine with no ``systemctl`` on it -- macOS, Windows, a container."""

    @pytest.fixture
    def manager(self) -> ServiceManager:
        return SystemdUserServiceManager(UNIT, run=_missing_systemctl)


class TestWhatTheSystemdAdapterAsksFor:
    def test_it_always_passes_user(self) -> None:
        # core/05 section 11: a *per-user* service. A system-wide `systemctl`
        # would need root and would manage somebody else's process.
        systemctl = FakeSystemctl()
        manager = SystemdUserServiceManager(UNIT, run=systemctl)
        manager.status()
        manager.start()
        manager.stop()
        assert all(command[:2] == ("systemctl", "--user") for command in systemctl.commands)

    def test_it_reads_state_through_show_rather_than_is_active(self) -> None:
        # `is-active` cannot distinguish "stopped" from "no such unit"; `show`
        # reports LoadState, which can.
        systemctl = FakeSystemctl()
        SystemdUserServiceManager(UNIT, run=systemctl).status()
        assert systemctl.commands[0][2] == "show"

    def test_it_requests_only_the_three_state_properties(self) -> None:
        # `systemctl show` with no property filter dumps the unit's whole
        # environment, which is a way to put a credential in a log
        # (details/security-configuration.md section 9).
        systemctl = FakeSystemctl()
        SystemdUserServiceManager(UNIT, run=systemctl).status()
        requested = {
            argument.removeprefix("--property=")
            for argument in systemctl.commands[0]
            if argument.startswith("--property=")
        }
        assert requested == {"LoadState", "ActiveState", "SubState"}

    def test_it_names_the_configured_unit(self) -> None:
        systemctl = FakeSystemctl()
        SystemdUserServiceManager("never4ga-test.service", run=systemctl).status()
        assert "never4ga-test.service" in systemctl.commands[0]


class TestWhatTheSystemdAdapterMakesOfTheAnswer:
    @pytest.mark.parametrize(
        ("active_state", "expected"),
        [
            ("active", ServiceState.RUNNING),
            ("inactive", ServiceState.STOPPED),
            ("failed", ServiceState.FAILED),
            ("activating", ServiceState.STARTING),
            ("deactivating", ServiceState.STOPPING),
            ("reloading", ServiceState.RUNNING),
            ("something-new", ServiceState.UNKNOWN),
        ],
    )
    def test_it_maps_active_state(self, active_state: str, expected: ServiceState) -> None:
        manager = SystemdUserServiceManager(UNIT, run=FakeSystemctl(active_state=active_state))
        assert manager.status().state is expected

    def test_an_unknown_unit_is_not_installed_rather_than_stopped(self) -> None:
        # The difference decides what the CLI tells the user: install the unit,
        # or start it.
        manager = SystemdUserServiceManager(UNIT, run=FakeSystemctl(load_state="not-found"))
        assert manager.status().state is ServiceState.NOT_INSTALLED

    def test_a_not_installed_unit_cannot_be_started(self) -> None:
        manager = SystemdUserServiceManager(UNIT, run=FakeSystemctl(load_state="not-found"))
        with pytest.raises(ServiceControlError, match="not installed"):
            manager.start()

    def test_a_failing_command_is_reported_with_its_stderr(self) -> None:
        def failing(command: Sequence[str]) -> CommandResult:
            if command[2] == "show":
                return CommandResult(0, "LoadState=loaded\nActiveState=inactive\nSubState=dead\n")
            return CommandResult(1, "", "Failed to start never4ga.service: Unit not found.")

        manager = SystemdUserServiceManager(UNIT, run=failing)
        with pytest.raises(ServiceControlError, match="Unit not found"):
            manager.start()

    def test_unparsable_output_is_unknown_rather_than_an_exception(self) -> None:
        manager = SystemdUserServiceManager(UNIT, run=lambda _: CommandResult(0, "???"))
        assert manager.status().state is ServiceState.UNKNOWN

    def test_the_detail_names_the_unit(self) -> None:
        manager = SystemdUserServiceManager(UNIT, run=FakeSystemctl(active_state="active"))
        assert UNIT in manager.status().detail


class TestTheFakeIsAServiceManager:
    def test_it_starts_stopped(self) -> None:
        assert FakeServiceManager().status().state is ServiceState.STOPPED

    def test_it_can_be_asked_to_fail(self) -> None:
        # core/05 section 19: callers have to be testable against a subsystem
        # that is present and broken, not only one that is absent.
        manager = FakeServiceManager(state=ServiceState.FAILED)
        assert manager.status().state is ServiceState.FAILED
