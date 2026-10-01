"""Which clients should be told about the MCP server, and what would be run.

Never4gA registers `never4ga-mcp` with the clients on this machine, through the
client descriptors, behind the same `--apply` every outward-facing write has. A
client's configuration is somebody's machine state rather than Never4gA's.

**A plan is a list of commands a person can read.** That is the whole reason
this is a service and the running of them is an adapter: what is about to happen
has to be visible before it happens, and an argv renders. `core/09` asks that
provider configuration stay adapter-owned; calling a client's own verb is the
strongest form of that -- Never4gA never learns the format at all.

A client that is not installed is skipped, not attempted. A client whose
descriptor does not say how to register is skipped too, and says which: that is
a fact about the client rather than a gap to route around.

**Re-registering needs saying so.** The supported clients disagree about what a
second `mcp add` means -- Antigravity updates, Codex overwrites, and Claude Code
refuses with "already exists" -- so converging on one behaviour means choosing
one. ``replace`` removes first, and it is not the default: a server already
named `never4ga` might be one a person put there, and `core/09`'s rule against
overwriting an unmanaged entry is not satisfied by a name collision looking
convenient.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from never4ga.domain.clients import ClientDescriptor
from never4ga.ports.client_registry import ClientRegistry, RegistrationOutcome

__all__ = [
    "SERVER_COMMAND",
    "SERVER_NAME",
    "McpRegistration",
    "McpRegistrationService",
    "RegistrationPlan",
]

#: What the server is called in every client. One name, so a person looking at
#: three clients sees the same thing in each.
SERVER_NAME: Final = "never4ga"

#: The executable a client is told to run.
#:
#: **An absolute path, resolved by the caller.** A client that cannot find a
#: bare name on its own `PATH` stores a server it can never start, which is
#: worse than storing none. A client spawned from a desktop session has a
#: different environment from the shell that registered it, so the name is
#: never enough.
SERVER_COMMAND: Final = "never4ga-mcp"


@dataclass(frozen=True, slots=True)
class RegistrationPlan:
    """What would be run for one client, or why nothing would."""

    client_id: str
    title: str
    command: tuple[str, ...] = ()
    #: Set when there is nothing to run, saying which of the two reasons it is.
    skipped: str | None = None

    @property
    def runnable(self) -> bool:
        return self.skipped is None and bool(self.command)

    def render(self) -> str:
        if self.skipped is not None:
            return f"  {self.title:<18} skipped: {self.skipped}"
        return f"  {self.title:<18} {' '.join(self.command)}"


@dataclass(frozen=True, slots=True)
class McpRegistration:
    """The plan, and what came of it."""

    plans: tuple[RegistrationPlan, ...]
    outcomes: tuple[RegistrationOutcome, ...] = ()
    applied: bool = False

    def render(self) -> str:
        lines = [f"register {SERVER_NAME} with {len(self.plans)} client(s)"]
        lines += [plan.render() for plan in self.plans]
        if not self.applied:
            lines.append("  nothing was run (pass --apply to register)")
            return "\n".join(lines)
        for outcome in self.outcomes:
            mark = "ok" if outcome.ok else "failed"
            lines.append(
                f"  {outcome.client_id:<18} {mark}{': ' + outcome.detail if outcome.detail else ''}"
            )
        return "\n".join(lines)


class McpRegistrationService:
    """Plans the registration, and runs it only when told to."""

    def __init__(
        self,
        descriptors: Sequence[ClientDescriptor],
        *,
        home: Path,
        registry: ClientRegistry,
        server: Path | None,
        vault: Path | None = None,
    ) -> None:
        self._descriptors = tuple(descriptors)
        self._home = home
        self._registry = registry
        self._server = server
        self._vault = vault

    def plan(self) -> tuple[RegistrationPlan, ...]:
        return tuple(self._plan_one(descriptor) for descriptor in self._descriptors)

    def _plan_one(self, descriptor: ClientDescriptor) -> RegistrationPlan:
        if not descriptor.is_installed(self._home):
            return RegistrationPlan(
                descriptor.client_id, descriptor.title, skipped="not installed on this machine"
            )
        if not descriptor.manages_mcp:
            return RegistrationPlan(
                descriptor.client_id,
                descriptor.title,
                skipped="its descriptor does not say how to register an MCP server",
            )
        if self._server is None:
            return RegistrationPlan(
                descriptor.client_id,
                descriptor.title,
                skipped=f"{SERVER_COMMAND} is not installed anywhere this can find",
            )
        # The vault travels as an argument rather than as environment, so the
        # command a person reads is the command that runs.
        arguments = () if self._vault is None else ("--vault", str(self._vault))
        return RegistrationPlan(
            descriptor.client_id,
            descriptor.title,
            command=descriptor.mcp_add_command(SERVER_NAME, str(self._server), *arguments),
        )

    def register(self, *, apply: bool = False, replace: bool = False) -> McpRegistration:
        plans = self.plan()
        if not apply:
            return McpRegistration(plans=plans)
        outcomes: list[RegistrationOutcome] = []
        for descriptor, plan in zip(self._descriptors, plans, strict=True):
            if not plan.runnable:
                continue
            if replace and descriptor.mcp_remove_arguments:
                # Its result is deliberately not collected: removing something
                # that is not there is not a failure, and the add that follows
                # is the outcome that matters.
                self._registry.register(descriptor, descriptor.mcp_remove_command(SERVER_NAME))
            outcomes.append(self._registry.register(descriptor, plan.command))
        return McpRegistration(plans=plans, outcomes=tuple(outcomes), applied=True)
