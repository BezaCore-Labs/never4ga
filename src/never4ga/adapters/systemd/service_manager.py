"""SystemdUserServiceManager -- ``systemctl --user`` behind the port.

core/05 section 11: the first platform service adapter supports
``systemd --user``. It reports and controls; it does not install, so nothing
here writes a file anywhere.

Two details are deliberate. State is read through ``systemctl show`` rather
than ``is-active``, because ``is-active`` cannot distinguish a stopped unit
from a unit that does not exist, and those call for different advice. And
``show`` is given an explicit property filter: unfiltered, it prints the unit's
entire environment, which is a way to put a credential in a log
(`details/security-configuration.md` section 9).

The command runner is injectable so the adapter's real work -- the command it
issues and the output it parses -- is testable on a machine with no systemd.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from never4ga.errors import ServiceControlError
from never4ga.ports.service_manager import ServiceState, ServiceStatus

__all__ = ["CommandResult", "CommandRunner", "SystemdUserServiceManager"]

DEFAULT_UNIT: Final = "never4ga.service"

_PROPERTIES: Final = ("LoadState", "ActiveState", "SubState")

#: systemd's ActiveState vocabulary. ``reloading`` is a running unit rereading
#: its configuration, which is running as far as a caller is concerned.
_ACTIVE_STATES: Final = {
    "active": ServiceState.RUNNING,
    "reloading": ServiceState.RUNNING,
    "inactive": ServiceState.STOPPED,
    "failed": ServiceState.FAILED,
    "activating": ServiceState.STARTING,
    "deactivating": ServiceState.STOPPING,
}

_TIMEOUT_SECONDS: Final = 30


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


#: Anything that can run a command line and report what happened.
CommandRunner = Callable[[Sequence[str]], CommandResult]


class SystemdUserServiceManager:
    """A ServiceManager over one ``systemd --user`` unit."""

    def __init__(self, unit: str = DEFAULT_UNIT, *, run: CommandRunner | None = None) -> None:
        self._unit = unit
        self._run = _run_systemctl if run is None else run

    @property
    def unit(self) -> str:
        return self._unit

    @property
    def can_control(self) -> bool:
        return self.status().state is not ServiceState.UNSUPPORTED

    def status(self) -> ServiceStatus:
        try:
            result = self._run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    self._unit,
                    *(f"--property={name}" for name in _PROPERTIES),
                ]
            )
        except (FileNotFoundError, NotADirectoryError, PermissionError) as error:
            # No systemctl on this machine. core/05 section 11 makes other
            # platforms later extensions, so this is expected rather than
            # broken -- and it must not stop `never4ga service status` from
            # answering.
            return ServiceStatus(
                ServiceState.UNSUPPORTED,
                f"systemd is not available on this machine ({error})",
            )
        except OSError as error:
            return ServiceStatus(ServiceState.UNKNOWN, f"could not ask systemd: {error}")

        if not result.ok:
            return ServiceStatus(
                ServiceState.UNKNOWN,
                f"systemctl could not report on {self._unit}: {_summarize(result)}",
            )
        return self._interpret(_properties(result.stdout))

    def start(self) -> ServiceStatus:
        return self._control("start")

    def stop(self) -> ServiceStatus:
        return self._control("stop")

    def restart(self) -> ServiceStatus:
        return self._control("restart")

    # -- internals --------------------------------------------------------

    def _control(self, verb: str) -> ServiceStatus:
        current = self.status()
        if current.state is ServiceState.UNSUPPORTED:
            raise ServiceControlError(
                f"cannot {verb} {self._unit}: {current.detail}. Run `never4ga serve` "
                "in the foreground instead."
            )
        if current.state is ServiceState.NOT_INSTALLED:
            raise ServiceControlError(
                f"cannot {verb} {self._unit}: the unit is not installed. "
                "Install the documented unit file first; Never4gA does not write "
                "into ~/.config/systemd/user/."
            )
        try:
            result = self._run(["systemctl", "--user", verb, self._unit])
        except OSError as error:
            raise ServiceControlError(f"cannot {verb} {self._unit}: {error}") from error
        if not result.ok:
            raise ServiceControlError(f"cannot {verb} {self._unit}: {_summarize(result)}")
        return self.status()

    def _interpret(self, properties: dict[str, str]) -> ServiceStatus:
        load_state = properties.get("LoadState", "")
        if load_state == "not-found":
            return ServiceStatus(
                ServiceState.NOT_INSTALLED,
                f"{self._unit} is not installed for this user",
            )
        active_state = properties.get("ActiveState")
        if active_state is None:
            return ServiceStatus(
                ServiceState.UNKNOWN,
                f"systemd reported no ActiveState for {self._unit}",
            )
        state = _ACTIVE_STATES.get(active_state, ServiceState.UNKNOWN)
        sub_state = properties.get("SubState", "")
        suffix = f" ({sub_state})" if sub_state else ""
        return ServiceStatus(state, f"{self._unit} is {active_state}{suffix}")


def _properties(output: str) -> dict[str, str]:
    """Parse ``Key=value`` lines, ignoring anything that is not one."""
    parsed: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            parsed[key.strip()] = value.strip()
    return parsed


def _summarize(result: CommandResult) -> str:
    """What went wrong, in one line, from the streams systemctl wrote."""
    message = result.stderr.strip() or result.stdout.strip()
    return message or f"systemctl exited {result.returncode}"


def _run_systemctl(command: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT_SECONDS,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)
