"""Scope types (core/07 Stage A).

Value objects only. The registry and resolver that produce a
:class:`ResolvedScope` are services and live in ``never4ga.context.scope``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason

__all__ = ["ResolvedScope", "ScopeRequest", "WorkspaceMapping"]


@dataclass(frozen=True, slots=True)
class ScopeRequest:
    """Deterministic inputs a caller can offer. All are optional."""

    cwd: PurePath | None = None
    repository_root: PurePath | None = None
    workspace_id: ConceptId | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceMapping:
    """One registry entry: a workspace, where it lives, and its repo if any.

    The repository root is machine-local; the workspace UUID is portable
    (core/05 section 9).
    """

    workspace_id: ConceptId
    workspace_path: VaultPath
    repository_root: PurePath | None = None
    parent_id: ConceptId | None = None
    #: Whether this repository's agent contract is part of what it *ships*.
    #:
    #: details/agent-instruction-layering.md section 8. The default is False,
    #: which means the repository gets a generated, gitignored pointer like
    #: every other. True means it authors and tracks its own `AGENTS.md`, and
    #: Never4gA writes no pointer into it at all.
    #:
    #: It is a field here rather than a name in the sync engine on purpose: a
    #: special case in the registry is a row, and a special case in the engine
    #: is a branch that grows a second one.
    ships_agent_contract: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The mechanically determined scope for a context request."""

    workspace_id: ConceptId
    workspace_path: VaultPath
    reason: AcquisitionReason
    parent_chain: tuple[ConceptId, ...] = ()
    repository_root: PurePath | None = None
