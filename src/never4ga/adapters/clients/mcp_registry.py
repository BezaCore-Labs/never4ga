"""Registering an MCP server by asking the client to do it.

Every client with a shipped descriptor has an ``mcp add`` verb of its own, and
Never4gA calls it rather than writing the client's configuration file. The
clients' file formats differ and are not reliably documented, so writing them
would mean inventing structure. Calling the client's own verb leaves format
changes, merge semantics and `core/09`'s "never overwrite an unmanaged MCP
server" with the client. Nothing is overwritten because nothing here opens a
file.

**An adapter, because it runs a process.** Like `adapters/git` and
`adapters/systemd`, this is an adapter because a service written against ports
must not know that the machine has executables on it.

**Nothing here decides.** It runs an argv somebody already approved and reports
what happened. Whether to run one at all is `services/mcp_registration.py`, and
whether *this* invocation applies is the caller's ``--apply``.
"""

from __future__ import annotations

import subprocess
from typing import Final

from never4ga.domain.clients import ClientDescriptor
from never4ga.ports.client_registry import RegistrationOutcome

__all__ = ["SubprocessClientRegistry"]

#: Long enough for a client to rewrite a config file, short enough that a
#: hanging client does not hang the caller.
TIMEOUT: Final = 30.0


class SubprocessClientRegistry:
    """Runs the client's own command, and never raises on its behalf."""

    def register(
        self, descriptor: ClientDescriptor, command: tuple[str, ...]
    ) -> RegistrationOutcome:
        return _run(descriptor, command)


def _run(descriptor: ClientDescriptor, command: tuple[str, ...]) -> RegistrationOutcome:
    try:
        finished = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return RegistrationOutcome(
            descriptor.client_id, command, ok=False, detail=f"{command[0]} is not on PATH"
        )
    except subprocess.TimeoutExpired:
        return RegistrationOutcome(
            descriptor.client_id,
            command,
            ok=False,
            detail=f"{command[0]} did not finish within {TIMEOUT:.0f}s",
        )
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip().splitlines()
        return RegistrationOutcome(
            descriptor.client_id,
            command,
            ok=False,
            detail=detail[-1] if detail else f"exited {finished.returncode}",
        )
    return RegistrationOutcome(
        descriptor.client_id, command, ok=True, detail=(finished.stdout or "").strip()
    )
