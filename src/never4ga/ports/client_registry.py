"""ClientRegistry -- telling an agent client about an MCP server.

The implementation runs the client's own `mcp add` verb. That means running a
process, which makes it an adapter: a service written against ports must not
know the machine has executables on it.

`core/09`: "MCP servers may be managed centrally, but provider configs remain
adapter-owned." Calling a client's own verb is the strongest reading of that.
Never4gA learns no format, opens no file of somebody else's, and the rule about
never overwriting an unmanaged server needs no enforcement here because nothing
here overwrites anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from never4ga.domain.clients import ClientDescriptor

__all__ = ["ClientRegistry", "RegistrationOutcome"]


@dataclass(frozen=True, slots=True)
class RegistrationOutcome:
    """What telling one client did.

    ``ok`` rather than an exception, because registering three clients must not
    stop at the first one that is unhappy: a client that is missing, refuses or
    hangs is a fact to report beside the two that worked.
    """

    client_id: str
    command: tuple[str, ...]
    ok: bool
    detail: str = ""


@runtime_checkable
class ClientRegistry(Protocol):
    def register(
        self, descriptor: ClientDescriptor, command: tuple[str, ...]
    ) -> RegistrationOutcome:
        """Run one client's own command. Never raises on the client's behalf."""
        ...
