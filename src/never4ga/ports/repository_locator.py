"""RepositoryLocator -- which repository a directory is in, and what it names.

`MechanicalScopeResolver` resolves from deterministic inputs and touches no
filesystem, which is the right shape for the precedence rules but leaves two
mechanical questions unanswered: given a working directory, which repository
contains it, and does that repository name a workspace?

Both are filesystem questions, so they live behind a port rather than inside
`never4ga.context`. That keeps scope resolution testable without a real
repository on disk, and keeps the resolver's precedence logic -- the part that is
actually subtle -- free of directory walking.

**The marker.** `.never4ga.toml` at a repository root, holding
``workspace = "<uuid>"`` and nothing else. TOML because a person may read and
edit it, and `tomllib` reads it with no dependency. A UUID rather than a name
because identity is a UUIDv7 and a name breaks on rename (core/02 section 5.1).

**Mechanism, not policy.** details/security-configuration.md section 7.1 permits
writing a Never4gA-owned file into a *mapped* repository, on an explicit
command, with a diff shown and ownership recorded. Those conditions belong to the service that calls
:meth:`write_marker`, not to this port. A locator that enforced them would make
them impossible to test where they are actually decided.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Final, Protocol, runtime_checkable

from never4ga.domain.identity import ConceptId

__all__ = ["MARKER_FILENAME", "MARKER_WORKSPACE_KEY", "RepositoryLocator"]

#: Lowercase, and a single field needs no separator.
MARKER_FILENAME: Final = ".never4ga.toml"

#: The marker's only key. On the port rather than in an adapter because a caller
#: that shows a user what will be written has to agree with the writer.
MARKER_WORKSPACE_KEY: Final = "workspace"


@runtime_checkable
class RepositoryLocator(Protocol):
    def repository_root(self, start: PurePath) -> PurePath | None:
        """The nearest repository containing ``start``, or None if there is none.

        Nearest rather than outermost: a repository vendored inside another
        belongs to itself. Returning None keeps an unrelated repository from
        resolving to a guessed workspace.
        """
        ...

    def main_worktree(self, repository_root: PurePath) -> PurePath | None:
        """The main worktree a linked worktree was made from, or None.

        A worktree made with `git worktree add` is a
        repository root of its own, and :meth:`repository_root` rightly says
        so, but nobody maps it: worktrees are made per task and thrown away.
        Resolution asks this only after the registry has nothing to say about
        the worktree, and looks the answer up instead.

        None for an ordinary clone; for a submodule, whose `.git` is also a
        file but which is a repository of its own; for a worktree of a bare
        repository, which has no main checkout to be mapped; and whenever the
        question cannot be answered. Not knowing is not an error here, since
        the caller already has a refusal to give.
        """
        ...

    def visible(self, path: PurePath) -> bool:
        """Is ``path`` there at all, as this process sees it?

        Not the same question as :meth:`repository_root`
        returning None: a directory in no repository is visible, and resolving
        it rightly fails as unmapped. A path that is not visible is one the
        caller could see and this process cannot, such as a service sandboxed
        with its own `/tmp`. Failing it as unmapped would blame a mapping that
        may well be there.
        """
        ...

    def read_marker(self, repository_root: PurePath) -> ConceptId | None:
        """The workspace a repository names, or None if it names none.

        Raises :class:`~never4ga.errors.RepositoryMarkerError` when a marker is
        present but unusable.
        """
        ...

    def write_marker(self, repository_root: PurePath, workspace_id: ConceptId) -> PurePath:
        """Write the marker, replacing any existing one. Returns what was written."""
        ...

    def exists(self, repository_root: PurePath, relative: str) -> bool:
        """Is there a file or directory at ``relative`` under ``repository_root``?

        Lets `doctor` tell whether a path an agent file names is still there. A
        repository that is mapped but not checked out answers ``False`` to
        everything, which is why the caller checks the root separately rather
        than reading a wall of missing paths.

        ``repository_root`` is normally a repository, and need not be: an agent
        file may name a path under the user's home -- `~/.claude/CLAUDE.md` is
        the machine-wide conventions file -- and asking about it is the same
        question against a different root. This is the port for "does something
        outside the vault exist", which is why the answer does not come from a
        service reaching for the filesystem itself.
        """
        ...

    def read_text(self, repository_root: PurePath, relative: str) -> str | None:
        """The file's contents, or ``None`` when it is absent or unreadable.

        ``None`` rather than an exception for an unreadable file: `doctor` is
        the one caller, it reports rather than repairs, and a permission error
        on one file must not end the diagnosis of everything else.
        """
        ...

    def write_text(self, repository_root: PurePath, relative: str, content: str) -> None:
        """Write ``content`` to ``relative``, creating parent directories.

        Writing a generated pointer into a *mapped* repository is a normal path,
        governed by the six conditions of details/security-configuration.md
        section 7.1 (and details/agent-instruction-layering.md section 6).
        Those belong to the service that calls this, for the reason
        :meth:`write_marker` gives: a port that enforced them would make them
        untestable where they are actually decided.

        Replaces an existing file, and writing the same content twice is a
        no-op that leaves the same bytes -- `details/api-cli-mcp-contract.md`
        section 11 requires sync to be idempotent.
        """
        ...
