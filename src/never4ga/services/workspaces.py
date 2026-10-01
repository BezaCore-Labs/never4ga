"""Workspace resolution against machine-local state (core/07 Stage A).

`MechanicalScopeResolver` decides precedence between deterministic inputs and
touches nothing. This service supplies the inputs it cannot know by itself:
which repository a working directory is in, and what that repository says about
itself.

**Precedence.** The machine-local registry outranks a repository marker, because
authority ranks before specificity: a marker committed to a shared repository
may have been written by somebody else, while the registry is this user's own
machine. A disagreement is *reported* rather than silently resolved, so nobody
has to wonder why a workspace resolved somewhere unexpected.

**Nothing resolves outside the vault in hand.** Given the vault's
documents, a resolution is refused unless the vault holds the workspace it
names, at the path the mapping records. Mappings are kept per vault, so this is
the second lock rather than the first; it is also what notices a mapping left
pointing at a workspace that has since moved.

**Nothing is guessed.** An unmapped repository with no marker raises rather than
inferring a workspace from a directory name.

The store is read on every call rather than cached. Mappings change from another
process -- the CLI writes them while a service is running -- and a resolver
serving a stale registry would be wrong in the one way that is hardest to
notice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Final

from never4ga.context.scope import (
    MechanicalScopeResolver,
    WorkspaceRegistry,
)
from never4ga.domain.deployments import Deployment, tree_hash
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest, WorkspaceMapping
from never4ga.errors import (
    PathNotVisibleError,
    RepositoryMarkerError,
    ScopeResolutionError,
    StructuredError,
)
from never4ga.ports.deployments import DeploymentStore
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.repository_locator import (
    MARKER_FILENAME,
    MARKER_WORKSPACE_KEY,
    RepositoryLocator,
)
from never4ga.ports.workspace_mappings import WorkspaceMappingStore
from never4ga.services.authoring import Clock, format_timestamp, utc_now

#: The manifest records repository markers alongside deployed Skills. A marker
#: is not a client, but it is the same kind of fact: something Never4gA wrote
#: outside the vault, and may only replace while it can prove it wrote it.
_MARKER_CLIENT: Final = "repository_marker"

__all__ = ["ScopeResolution", "WorkspaceService", "scope_refusal", "vault_holds"]


def scope_refusal(
    error: ScopeResolutionError | RepositoryMarkerError, where: PurePath | str
) -> StructuredError:
    """How every interface reports a scope that did not resolve.

    One place, so that every interface tells a path this process cannot see
    apart from a repository nobody mapped.
    """
    if isinstance(error, PathNotVisibleError):
        return StructuredError(
            "path_not_visible",
            str(error),
            {"path": str(where)},
            repair_hint=(
                "work from a path this process can see; if a service answered, "
                "pass --local, or keep the worktree outside /tmp"
            ),
        )
    return StructuredError(
        "scope_unresolved",
        str(error),
        {"path": str(where)},
        repair_hint="map the repository with `never4ga workspace map <workspace-id>`",
    )


def vault_holds(documents: DocumentStore, mapping: WorkspaceMapping | ResolvedScope) -> bool:
    """Whether this vault has the workspace a mapping names, where it says.

    By identity as well as location: a workspace that moved leaves its path to
    whatever arrives next, and the mapping names the workspace, not the path
    (core/02 section 5.1).
    """
    document = documents.get_by_path(mapping.workspace_path)
    return document is not None and document.concept_id == mapping.workspace_id


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    """A resolved scope, and anything the caller should be told about it.

    ``conflicts`` is empty in the ordinary case. It carries the disagreement
    between a registry and a marker rather than raising, because the resolution
    itself is not in doubt -- the registry wins -- and refusing to work would be
    a worse answer than working and saying so.
    """

    scope: ResolvedScope
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkOutcome:
    """What `workspace mark` would write, or did."""

    marker: PurePath
    content: str
    written: bool


class WorkspaceService:
    def __init__(
        self,
        store: WorkspaceMappingStore,
        locator: RepositoryLocator,
        *,
        documents: DocumentStore | None = None,
    ) -> None:
        """``documents`` is the vault in hand, and every composition root passes it.

        Optional so that a test of precedence need not build workspaces, and
        `test_composition_parity.py` is what holds the roots to it.
        """
        self._store = store
        self._locator = locator
        self._documents = documents

    # -- the registry -----------------------------------------------------

    def mappings(self) -> Sequence[WorkspaceMapping]:
        """Every mapping this machine holds, in a stable order."""
        return tuple(sorted(self._store.load(), key=lambda m: str(m.workspace_path)))

    def map(
        self,
        *,
        workspace_id: ConceptId,
        workspace_path: VaultPath,
        repository_root: PurePath | None = None,
        parent_id: ConceptId | None = None,
        ships_agent_contract: bool = False,
    ) -> WorkspaceMapping:
        """Record where a workspace lives on this machine.

        Replaces any earlier note for the *same repository* -- a repository
        belongs to one workspace at a time, and re-mapping it is a correction.
        A second repository for the same workspace is a second entry, not a
        replacement: a workspace may own several.
        """
        mapping = WorkspaceMapping(
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            repository_root=repository_root,
            parent_id=parent_id,
            ships_agent_contract=ships_agent_contract,
        )

        def replaced(earlier: WorkspaceMapping) -> bool:
            if repository_root is None:
                return earlier.workspace_id == workspace_id and earlier.repository_root is None
            return earlier.repository_root == repository_root

        kept = [m for m in self._store.load() if not replaced(m)]
        self._store.save([*kept, mapping])
        return mapping

    def mark(
        self,
        repository_root: PurePath,
        *,
        deployments: DeploymentStore,
        apply: bool = False,
        now: Clock = utc_now,
    ) -> MarkOutcome:
        """Write `.never4ga.toml` into a mapped repository (core/04 section 14).

        Every condition `details/security-configuration.md` section 7.1
        attaches to a repository write is checked here, using the `core/04`
        section 25 ownership manifest:

        1. **mapped** -- an unmapped repository is refused outright;
        2. **explicit** -- this runs from a verb a person typed, never on
           discovery and never on a timer;
        3. **diff first** -- ``apply`` defaults to false and the caller is handed
           what would be written;
        4. **ownership recorded** -- the marker goes in the manifest with its
           content hash, like any other thing Never4gA writes outside the vault;
        6. **never a third-party repository** -- which is condition 1 again, from
           the other side.

        Condition 5 -- a delimited managed block rather than a wholesale
        overwrite -- is what makes this refuse. The marker is a file Never4gA
        creates whole, so the condition has nothing to constrain the first time.
        Once one exists, it is an existing file: if the manifest cannot prove
        Never4gA wrote it, the file is somebody else's and stays.
        """
        mapping = self._mapping_for(repository_root)
        if mapping is None:
            raise RepositoryMarkerError(
                f"{repository_root} is not mapped to a workspace; map it before "
                "marking it, so that a third-party repository is never written to"
            )

        root = mapping.repository_root or repository_root
        marker = root / MARKER_FILENAME
        content = f'{MARKER_WORKSPACE_KEY} = "{mapping.workspace_id}"\n'
        recorded = {
            item.name: item for item in deployments.load() if item.client_id == _MARKER_CLIENT
        }
        existing = self._locator.read_marker(root)
        ours = str(marker) in recorded

        if existing is not None and not ours:
            raise RepositoryMarkerError(
                f"there is already a marker at {marker} that Never4gA did not write; "
                "it is not ours to replace"
            )
        if not apply:
            return MarkOutcome(marker=marker, content=content, written=False)

        self._locator.write_marker(mapping.repository_root or repository_root, mapping.workspace_id)
        updated = [item for item in deployments.load() if item.name != str(marker)]
        updated.append(
            Deployment(
                client_id=_MARKER_CLIENT,
                name=str(marker),
                target=str(marker),
                source_path=str(mapping.workspace_path),
                source_hash=tree_hash({MARKER_FILENAME: content}),
                deployed_hash=tree_hash({MARKER_FILENAME: content}),
                deployed_at=format_timestamp(now()),
            )
        )
        deployments.save(updated)
        return MarkOutcome(marker=marker, content=content, written=True)

    def _mapping_for(self, repository_root: PurePath) -> WorkspaceMapping | None:
        for mapping in self._store.load():
            if mapping.repository_root == repository_root:
                return mapping
        return None

    def unmap(self, workspace_id: ConceptId) -> bool:
        """Forget a mapping. Returns whether there was one to forget."""
        remaining = [m for m in self._store.load() if m.workspace_id != workspace_id]
        if len(remaining) == len(self._store.load()):
            return False
        self._store.save(remaining)
        return True

    # -- resolution -------------------------------------------------------

    def resolve(self, request: ScopeRequest) -> ScopeResolution:
        try:
            resolution = self._resolve(request)
            self._confirm(resolution.scope)
            return resolution
        except PathNotVisibleError:
            raise
        except ScopeResolutionError as error:
            # Asked only once resolution has failed, because a mapped path
            # resolves from the registry whether or not it can be seen.
            unseen = self._unseen(request)
            if unseen is None:
                raise
            raise PathNotVisibleError(
                f"{unseen} does not exist as this process sees it, so no workspace "
                "can be resolved from it; a service running in a sandbox (systemd's "
                "PrivateTmp hides /tmp) cannot see every path its caller can"
            ) from error

    def _resolve(self, request: ScopeRequest) -> ScopeResolution:
        registry = self._registry()
        resolver = MechanicalScopeResolver(registry)

        if request.workspace_id is not None:
            return ScopeResolution(resolver.resolve(request))

        root = self._repository_root(request)
        if root is None:
            # No repository, so nothing mechanical is left to try. The resolver
            # produces the error, so the message stays in one place.
            return ScopeResolution(resolver.resolve(request))

        marker = self._locator.read_marker(root)
        mapping = registry.resolve_path(root)

        if mapping is None:
            main = self._locator.main_worktree(root)
            if main is not None and registry.resolve_path(main) is not None:
                return self._through_worktree(
                    self.resolve(ScopeRequest(repository_root=main)), root
                )
            return ScopeResolution(self._resolve_by_marker(registry, root, marker))

        if marker is None or marker == mapping.workspace_id:
            return ScopeResolution(resolver.resolve(ScopeRequest(repository_root=root)))

        return ScopeResolution(
            resolver.resolve(ScopeRequest(repository_root=root)),
            conflicts=(
                f"{root} is mapped to workspace {mapping.workspace_id}, but its "
                f"marker names {marker}; this machine's registry wins. Correct "
                f"whichever is wrong rather than leaving them to disagree.",
            ),
        )

    # -- internals --------------------------------------------------------

    def _confirm(self, scope: ResolvedScope) -> None:
        if self._documents is None or vault_holds(self._documents, scope):
            return
        raise ScopeResolutionError(
            f"workspace {scope.workspace_id} is mapped at {scope.workspace_path}, which "
            "this vault does not hold; it was mapped from another vault or has moved "
            "since. Map it again from this vault with `never4ga workspace map`, or "
            "`never4ga workspace unmap` it"
        )

    def _unseen(self, request: ScopeRequest) -> PurePath | None:
        """The path a failed request named, if this process cannot see it."""
        if request.workspace_id is not None:
            return None
        path = request.repository_root or request.cwd
        if path is None or self._locator.visible(path):
            return None
        return path

    @staticmethod
    def _through_worktree(resolution: ScopeResolution, worktree: PurePath) -> ScopeResolution:
        """The main checkout's resolution, pointed back at the worktree.

        The workspace, its parents and any marker conflict are the main
        checkout's. The repository root is the worktree's, because Git signals
        read it and the branch an agent is working on is the one checked out
        there.
        """
        scope = replace(
            resolution.scope,
            reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED, detail="worktree"),
            repository_root=worktree,
        )
        return replace(resolution, scope=scope)

    def _registry(self) -> WorkspaceRegistry:
        registry = WorkspaceRegistry()
        for mapping in self._store.load():
            registry.register(mapping)
        return registry

    def _repository_root(self, request: ScopeRequest) -> PurePath | None:
        """An explicit root is taken as given; a cwd is discovered from."""
        if request.repository_root is not None:
            return request.repository_root
        if request.cwd is None:
            return None
        return self._locator.repository_root(request.cwd)

    def _resolve_by_marker(
        self,
        registry: WorkspaceRegistry,
        root: PurePath,
        marker: ConceptId | None,
    ) -> ResolvedScope:
        """Resolve a repository the registry does not know, from its marker.

        This is what the marker is *for*: a repository cloned onto a machine that
        has never mapped it. The workspace itself must still be known here, since
        the marker names an identity and not a location in the vault.
        """
        if marker is None:
            raise ScopeResolutionError(
                f"{root} is not mapped to a workspace; register the repository "
                "rather than inferring a workspace"
            )
        mapping = registry.get(marker)
        if mapping is None:
            raise ScopeResolutionError(
                f"the marker in {root} names workspace {marker}, which is not "
                "registered on this machine; map it before relying on the marker"
            )
        return ResolvedScope(
            workspace_id=mapping.workspace_id,
            workspace_path=mapping.workspace_path,
            reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED, detail="repository_marker"),
            parent_chain=registry.parent_chain(mapping.workspace_id),
            repository_root=root,
        )
