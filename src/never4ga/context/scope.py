"""Stage A -- mechanical scope resolution (core/07 section 3).

Resolution order, most explicit first:

1. an explicit workspace ID;
2. an explicit repository root;
3. the current working directory, matched against the repo-to-workspace
   registry by longest path prefix.

An unmapped location is an error, never a guess: an unrelated repository must
not resolve to a workspace.

This resolver uses deterministic inputs only and touches no filesystem. Git-root
discovery and the optional repository marker live in the services layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Protocol, runtime_checkable

from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest, WorkspaceMapping
from never4ga.errors import ScopeResolutionError

__all__ = ["MAX_PARENT_DEPTH", "MechanicalScopeResolver", "ScopeResolver", "WorkspaceRegistry"]

#: Guards against a malformed registry sending traversal into an endless loop.
MAX_PARENT_DEPTH = 32


@runtime_checkable
class ScopeResolver(Protocol):
    """Anything that can say which workspace a request belongs to.

    :class:`MechanicalScopeResolver` resolves from deterministic inputs alone.
    A resolver that also knows about *this machine* -- discovering the repository
    around a directory, reading its marker -- lives in the services layer and
    fits here too, so a context assembler can be given either without knowing
    which it has.
    """

    def resolve(self, request: ScopeRequest) -> ResolvedScope: ...


@dataclass(slots=True)
class WorkspaceRegistry:
    """Machine-local repo-to-workspace mappings.

    A workspace may own several repositories (`core/03`'s `repositories` is
    plural), so entries are kept per (workspace, repository root), not per
    workspace.
    Re-registering the same root replaces its entry; everything a workspace
    shares across its roots (path, parent) is the same on every entry.
    """

    _mappings: dict[ConceptId, list[WorkspaceMapping]] = field(default_factory=dict)

    def register(self, mapping: WorkspaceMapping) -> None:
        bucket = self._mappings.setdefault(mapping.workspace_id, [])
        bucket[:] = [m for m in bucket if m.repository_root != mapping.repository_root]
        bucket.append(mapping)

    def get(self, workspace_id: ConceptId) -> WorkspaceMapping | None:
        bucket = self._mappings.get(workspace_id)
        return bucket[0] if bucket else None

    def resolve_path(self, path: PurePath) -> WorkspaceMapping | None:
        """Return the mapping whose repository root is the longest prefix of ``path``."""
        candidates = [
            mapping
            for bucket in self._mappings.values()
            for mapping in bucket
            if mapping.repository_root is not None and _is_within(path, mapping.repository_root)
        ]
        if not candidates:
            return None
        # The most specific mapping wins, so a child repo nested inside a parent
        # repo resolves to the child.
        return max(candidates, key=_repository_depth)

    def parent_chain(self, workspace_id: ConceptId) -> tuple[ConceptId, ...]:
        """Walk parents outward, nearest first."""
        chain: list[ConceptId] = []
        seen = {workspace_id}
        current = self.get(workspace_id)
        while current is not None and current.parent_id is not None:
            if current.parent_id in seen or len(chain) >= MAX_PARENT_DEPTH:
                raise ScopeResolutionError(
                    f"workspace parent chain for {workspace_id} contains a cycle"
                )
            chain.append(current.parent_id)
            seen.add(current.parent_id)
            current = self.get(current.parent_id)
        return tuple(chain)


def _repository_depth(mapping: WorkspaceMapping) -> int:
    return len(mapping.repository_root.parts) if mapping.repository_root is not None else 0


def _is_within(path: PurePath, root: PurePath) -> bool:
    return path == root or root.parts == path.parts[: len(root.parts)]


class MechanicalScopeResolver:
    """Resolves scope with no model call and no filesystem access."""

    def __init__(self, registry: WorkspaceRegistry) -> None:
        self._registry = registry

    def resolve(self, request: ScopeRequest) -> ResolvedScope:
        mapping, detail = self._locate(request)
        return ResolvedScope(
            workspace_id=mapping.workspace_id,
            workspace_path=mapping.workspace_path,
            reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED, detail=detail),
            parent_chain=self._registry.parent_chain(mapping.workspace_id),
            repository_root=mapping.repository_root,
        )

    def _locate(self, request: ScopeRequest) -> tuple[WorkspaceMapping, str]:
        if request.workspace_id is not None:
            mapping = self._registry.get(request.workspace_id)
            if mapping is None:
                raise ScopeResolutionError(
                    f"workspace {request.workspace_id} is not registered on this machine"
                )
            return mapping, "explicit_workspace_id"

        for path, detail in (
            (request.repository_root, "repository_root"),
            (request.cwd, "cwd"),
        ):
            if path is None:
                continue
            mapping = self._registry.resolve_path(path)
            if mapping is not None:
                return mapping, detail
            raise ScopeResolutionError(
                f"{path} is not mapped to a workspace; register the repository "
                "rather than inferring a workspace"
            )

        raise ScopeResolutionError(
            "no workspace ID, repository root or working directory was supplied"
        )
