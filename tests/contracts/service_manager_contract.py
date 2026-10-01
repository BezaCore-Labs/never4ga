"""ServiceManager contract.

Specification:
- core/05 section 20 -- ServiceManager is a port, so that core behaviour never
  depends semantically on systemd (section 11).
- core/05 section 11 -- the first platform adapter is ``systemd --user``;
  macOS and Windows are later extensions.
- The port reports and controls service state *without writing a unit file
  to do it*. There is no ``install``: installing one would write into another
  tool's directory, which details/security-configuration.md section 7 does not
  allow.

Two variants, because "no service manager on this platform" is a real answer
rather than a broken one. Every implementation satisfies
:class:`ServiceManagerContract`; one that can actually control a service adds
:class:`ControllableServiceManagerContract`, and one that cannot adds
:class:`UnsupportedServiceManagerContract` and must refuse rather than pretend.
"""

from __future__ import annotations

import pytest

from never4ga.errors import ServiceControlError
from never4ga.ports.service_manager import ServiceManager, ServiceState, ServiceStatus


class ServiceManagerContract:
    """True of every implementation, controllable or not."""

    @pytest.fixture
    def manager(self) -> ServiceManager:
        raise NotImplementedError("supply a ServiceManager fixture")

    def test_status_answers_without_raising(self, manager: ServiceManager) -> None:
        # `never4ga service status` must always be able to say something. A
        # reporting call that raises turns a diagnostic into a second failure.
        assert isinstance(manager.status(), ServiceStatus)

    def test_status_reports_a_known_state(self, manager: ServiceManager) -> None:
        assert manager.status().state in set(ServiceState)

    def test_status_is_a_read(self, manager: ServiceManager) -> None:
        first = manager.status()
        assert manager.status() == first

    def test_detail_is_human_readable_text(self, manager: ServiceManager) -> None:
        assert isinstance(manager.status().detail, str)

    def test_it_cannot_install_a_unit(self, manager: ServiceManager) -> None:
        # The absence is the point: a port with an `install` method invites
        # writing into ~/.config/systemd/user/.
        assert not hasattr(manager, "install")


class ControllableServiceManagerContract(ServiceManagerContract):
    """An implementation that can start and stop the service."""

    def test_it_declares_that_it_can_control(self, manager: ServiceManager) -> None:
        assert manager.can_control

    def test_start_makes_it_running(self, manager: ServiceManager) -> None:
        assert manager.start().state is ServiceState.RUNNING
        assert manager.status().state is ServiceState.RUNNING

    def test_start_is_idempotent(self, manager: ServiceManager) -> None:
        manager.start()
        assert manager.start().state is ServiceState.RUNNING

    def test_stop_makes_it_stopped(self, manager: ServiceManager) -> None:
        manager.start()
        assert manager.stop().state is ServiceState.STOPPED
        assert manager.status().state is ServiceState.STOPPED

    def test_stop_is_idempotent(self, manager: ServiceManager) -> None:
        manager.stop()
        assert manager.stop().state is ServiceState.STOPPED

    def test_restart_from_stopped_ends_running(self, manager: ServiceManager) -> None:
        manager.stop()
        assert manager.restart().state is ServiceState.RUNNING

    def test_restart_from_running_stays_running(self, manager: ServiceManager) -> None:
        manager.start()
        assert manager.restart().state is ServiceState.RUNNING

    def test_the_returned_status_agrees_with_a_fresh_read(self, manager: ServiceManager) -> None:
        # A command that reports success while `status` disagrees is worse than
        # one that fails: it is believed.
        assert manager.start() == manager.status()
        assert manager.stop() == manager.status()


class UnsupportedServiceManagerContract(ServiceManagerContract):
    """An implementation with no service manager behind it.

    core/05 section 19's failure principle applied to a port: reporting
    degrades to "unsupported", and control refuses loudly instead of returning
    a status nobody can act on.
    """

    def test_it_declares_that_it_cannot_control(self, manager: ServiceManager) -> None:
        assert not manager.can_control

    def test_status_says_unsupported(self, manager: ServiceManager) -> None:
        assert manager.status().state is ServiceState.UNSUPPORTED

    def test_status_explains_why(self, manager: ServiceManager) -> None:
        assert manager.status().detail

    @pytest.mark.parametrize("command", ["start", "stop", "restart"])
    def test_control_refuses_rather_than_pretending(
        self, manager: ServiceManager, command: str
    ) -> None:
        with pytest.raises(ServiceControlError):
            getattr(manager, command)()
