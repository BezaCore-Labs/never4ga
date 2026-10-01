"""Agent client descriptors (core/04 section 9).

An adapter is a *descriptor*, not a class. One sync engine reads a table of
these; supporting a fourth client is a new row and a test, never a new code
path. core/04 section 9 requires it and says why:

    tool filesystem paths are not part of Never4gA's permanent core schema --
    they are adapter implementation data

because "provider tools can change their discovery paths", and Never4gA "should
update one adapter rather than change the knowledge model". A documented path
can be wrong, so a descriptor records when it was last measured.

The type is pure. Where descriptors are *read from* -- shipped TOML, and the
user's own in the vault -- is :mod:`never4ga.client_descriptors`, and the
engine that acts on them takes them as arguments like any other input.
"""

from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["ClientDescriptor", "DeploymentMechanism"]


class DeploymentMechanism(enum.StrEnum):
    """How a canonical Skill reaches a client.

    Both work on all three shipped clients, and the default is ``COPY``
    deliberately (`core/04` section 24: "no symlink is
    required").

    A symlinked Skill is a client writing into the Git-backed vault. An agent
    editing what it takes to be its own Skill would rewrite the canonical copy
    for every client at once, and a vault on an auto-commit timer would carry it
    to every remote unreviewed. `core/04` section 25 rules 4 and 5 -- do not
    destroy an edit silently, report drift and require reconciliation -- need
    two copies to compare before they mean anything.

    ``SYMLINK`` stays available because it works and because a user may
    want the single-copy property knowing what it costs.
    """

    COPY = "copy"
    SYMLINK = "symlink"


@dataclass(frozen=True, slots=True)
class ClientDescriptor:
    """Everything the sync engine knows about one agent client.

    Paths are written the way a user writes them -- ``~/.claude/skills`` -- and
    resolve against a home directory supplied by the caller rather than read
    from the process, so a test never depends on the machine it runs on.
    """

    client_id: str
    title: str
    #: Where this client looks for Skills available to every project.
    global_skills_path: str
    #: Where it looks inside a repository, when it does. ``None`` means it has
    #: no per-workspace Skill location, which is a fact about the client rather
    #: than something Never4gA may work around.
    workspace_skills_path: str | None = None
    #: Where this client looks for subagent definitions, when it has any.
    #: ``None`` is the common case and not a gap: of the three shipped clients,
    #: only Claude Code has a native subagent format.
    #: Expressed as a descriptor field rather than a branch in the sync engine,
    #: so that a client which cannot take agents is *described* rather than
    #: special-cased -- the same shape `workspace_skills_path` already uses.
    #:
    #: An agent is a single file, unlike a Skill, which is a directory. That is
    #: the client's layout and not a choice here.
    global_agents_path: str | None = None
    #: The file layout of a Skill. Every client measured so far uses the open
    #: Agent Skills `SKILL.md` (core/04 section 20), and the field exists so
    #: that a client which does not is describable rather than unsupportable.
    skill_format: str = "agent_skills"
    deployment: DeploymentMechanism = DeploymentMechanism.COPY
    #: Any of these existing means the client is installed. Empty means the
    #: descriptor cannot tell, and nothing is ever deployed to it.
    detect_paths: tuple[str, ...] = field(default=())
    #: A command on `PATH` that also proves the client is installed.
    detect_command: str | None = None
    #: Where this client reads its global instructions
    #: (details/agent-instruction-layering.md section 3). Recorded for the
    #: managed-block half of sync; not used to place Skills.
    global_rules_path: str | None = None
    #: The filename this client reads *inside a repository*, when it is not
    #: `AGENTS.md`. A generated pointer goes at each of these so the name is
    #: occupied and the client cannot generate its own file there
    #: (details/agent-instruction-layering.md section 5.1).
    #:
    #: ``None`` means the client reads `AGENTS.md` natively and needs no file
    #: of its own -- Codex does -- which is the best outcome, because the file
    #: Never4gA does not write is the one that cannot be wrong.
    repository_rules_path: str | None = None
    #: How this client is told about an MCP server, as arguments to
    #: :attr:`detect_command`. Every client measured so far owns an `mcp add`
    #: verb of its own, and Never4gA calls it rather than writing the client's
    #: configuration file: the shapes differ, the formats change, and the merge
    #: semantics are the client's to get right. `core/09`'s "never overwrite an
    #: unmanaged MCP server" needs no enforcement when nothing is overwritten.
    #:
    #: ``{name}`` and ``{command}`` are substituted; anything after them is the
    #: server's own arguments. Empty means this client cannot be registered
    #: with, which is a fact about the client rather than a gap here.
    mcp_add_arguments: tuple[str, ...] = field(default=())
    mcp_list_arguments: tuple[str, ...] = field(default=())
    mcp_remove_arguments: tuple[str, ...] = field(default=())
    #: The date the paths above were last checked against the real client. A
    #: descriptor that cannot say when it was measured cannot be revalidated.
    measured_on: str = ""
    #: The client version the measurement was taken against.
    measured_version: str = ""
    notes: str = ""

    @property
    def manages_mcp(self) -> bool:
        return bool(self.detect_command and self.mcp_add_arguments)

    def mcp_add_command(self, name: str, command: str, *arguments: str) -> tuple[str, ...]:
        """The exact argv that registers a server with this client.

        Returned rather than run: what is about to happen is something a person
        should be able to read before it does, and a tuple renders.
        """
        if not self.manages_mcp:
            raise ValueError(f"{self.client_id} does not describe how to register an MCP server")
        rendered = [
            part.replace("{name}", name).replace("{command}", command)
            for part in self.mcp_add_arguments
        ]
        return (str(self.detect_command), *rendered, *arguments)

    def mcp_remove_command(self, name: str) -> tuple[str, ...]:
        """The argv that unregisters a server, for re-registering over one."""
        rendered = [part.replace("{name}", name) for part in self.mcp_remove_arguments]
        return (str(self.detect_command), *rendered)

    def global_skills_directory(self, home: Path) -> Path:
        return self._resolve(self.global_skills_path, home)

    @property
    def takes_agents(self) -> bool:
        """Whether this client has somewhere to put a subagent."""
        return self.global_agents_path is not None

    def global_agents_directory(self, home: Path) -> Path:
        if self.global_agents_path is None:
            raise ValueError(f"{self.client_id} has no agents directory")
        return self._resolve(self.global_agents_path, home)

    def is_installed(self, home: Path) -> bool:
        """Whether this client is on this machine.

        A descriptor that declares no signal is never installed. Deploying to a
        client that is not there would create the very directory the check looks
        for, which is indistinguishable from the client having been installed.
        """
        if any(self._resolve(candidate, home).exists() for candidate in self.detect_paths):
            return True
        return self.detect_command is not None and shutil.which(self.detect_command) is not None

    @staticmethod
    def _resolve(candidate: str, home: Path) -> Path:
        if candidate.startswith("~/"):
            return home / candidate[2:]
        if candidate == "~":
            return home
        return Path(candidate)
