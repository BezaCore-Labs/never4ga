"""Changing work items: the two gates, and what each refusal says.

**A service rather than a client verb**, and that is load-bearing. `core/05`
section 13 makes the CLI, the API and MCP thin clients over the *same*
application services, and none of them may carry its own business logic. Logic
kept in one client's verbs is logic the other clients either duplicate or lack.

What each *surface* supplies is the adapter. This module takes a writer factory
because `services` may not import `adapters`, and a composition root is the only
thing that picks one.

Every verb **proposes by default and sends nothing**: without ``applying`` it
returns the mutation it would make and reports that nothing was written.
`core/03` section 22 wants read, draft and actual write kept distinct, and a
draft that read like a receipt would be worse than no draft at all.

The workspace is resolved from where the caller is, the same way `context
startup` resolves it, because the workspace is what carries the declaration --
which tracker, which project, and how far Never4gA may go. A verb that took a
connection name instead would let a caller write to a tracker the workspace they
are standing in has nothing to do with.

**Which gate refuses is worth distinguishing, and these do.** A workspace with
no ``work_management`` has no tracker at all; one with ``sync_policy: read`` has
one and does not permit writing to it; one with ``read_write`` permits it and
still needs the invocation to say so. Three different sentences, because they
need three different actions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from never4ga.domain.connections import Connection
from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import ExternalId
from never4ga.domain.scope import ScopeRequest
from never4ga.domain.work_policy import WORK_MANAGEMENT_KEY, WriteDisposition, WritePolicy
from never4ga.errors import (
    ConnectionDefinitionError,
    Never4gaError,
    ProviderUnavailableError,
    RepositoryMarkerError,
    ScopeResolutionError,
    StructuredError,
    WorkItemNotFoundError,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.tracker_cache import TrackerCache
from never4ga.ports.work_management import ProposedMutation, WorkManagementWriter
from never4ga.services.connections import ConnectionRegistry
from never4ga.services.trackers import reference_key
from never4ga.services.work_reading import tracker_unreachable
from never4ga.services.workspaces import WorkspaceService, scope_refusal

__all__ = ["Bound", "WorkWriteService", "WriterFactory"]

type WriterFactory = Callable[[Connection], WorkManagementWriter | None]


@dataclass(frozen=True, slots=True)
class Bound:
    """A workspace, its tracker, and how far this invocation may go."""

    workspace: StoredDocument
    connection: Connection
    writer: WorkManagementWriter
    policy: WritePolicy

    @property
    def project(self) -> str:
        declared = self.workspace.frontmatter.get(WORK_MANAGEMENT_KEY)
        reference = declared.get("project_ref", "") if isinstance(declared, Mapping) else ""
        return str(reference) or (self.connection.project_ref or "")


class WorkWriteService:
    """The write verbs, over whatever adapter a composition root supplies."""

    def __init__(
        self,
        *,
        documents: DocumentStore,
        workspaces: WorkspaceService,
        factory: WriterFactory,
        cache: TrackerCache | None = None,
    ) -> None:
        self._documents = documents
        self._workspaces = workspaces
        self._factory = factory
        #: What the read path answers from, so a write can stop it contradicting
        #: itself. Optional because a test double need not have one, and
        #: a write must never depend on a derived store existing.
        self._cache = cache

    # -- binding ----------------------------------------------------------

    def bind(self, where: PurePath, *, applying: bool) -> Bound | StructuredError:
        """Resolve where the caller is into a workspace, a tracker and a policy."""
        try:
            resolution = self._workspaces.resolve(ScopeRequest(cwd=where))
        except (ScopeResolutionError, RepositoryMarkerError) as error:
            return scope_refusal(error, where)

        manifest = self._documents.get(resolution.scope.workspace_id)
        if manifest is None:
            return StructuredError(
                "workspace_missing",
                f"the resolved workspace {resolution.scope.workspace_id} is not in the vault",
                {"workspace": str(resolution.scope.workspace_id)},
            )

        declared = manifest.frontmatter.get(WORK_MANAGEMENT_KEY)
        policy = WritePolicy.from_declaration(
            declared if isinstance(declared, Mapping) else None, applying=applying
        )
        if not policy.has_external_tracker:
            return StructuredError(
                "work_management_undeclared",
                f"workspace {manifest.frontmatter.get('title')!r} declares no external tracker",
                {"workspace": str(manifest.concept_id), "mode": policy.mode or "none"},
                repair_hint="link one with `never4ga workspace link <connection>`",
            )

        name = str(declared.get("connection", "")) if isinstance(declared, Mapping) else ""
        try:
            connection = ConnectionRegistry(documents=self._documents).resolve(name)
        except ConnectionDefinitionError as error:
            return StructuredError(
                "connection_undefined",
                str(error),
                {"connection": name},
                repair_hint="define it as an `integration` concept in 50_System/Integrations/",
            )

        writer = self._factory(connection)
        if writer is None:
            return StructuredError(
                "connection_not_writable",
                f"no writable adapter for connection {connection.name!r} on this machine",
                {"connection": connection.name, "provider": connection.provider},
                repair_hint=(
                    f"store a token with `never4ga connection set-token {connection.name}`"
                ),
            )
        return Bound(workspace=manifest, connection=connection, writer=writer, policy=policy)

    # -- the verbs --------------------------------------------------------

    def update(
        self, bound: Bound, reference: str, fields: Mapping[str, Any]
    ) -> dict[str, Any] | StructuredError:
        refused = self._gated(bound, f"update work item {reference}")
        if refused is not None:
            return refused
        try:
            proposal = bound.writer.propose_update(
                ExternalId(provider=bound.writer.provider_id, value=reference), fields
            )
        except Never4gaError as error:
            return _not_proposed(bound, error, {"work_item": reference})
        return self._outcome(bound, proposal, touching=reference)

    def create(self, bound: Bound, fields: Mapping[str, Any]) -> dict[str, Any] | StructuredError:
        refused = self._gated(bound, "create a work item")
        if refused is not None:
            return refused
        try:
            proposal = bound.writer.propose_create(bound.project, fields)
        except Never4gaError as error:
            return _not_proposed(bound, error, {"project": bound.project})
        return self._outcome(bound, proposal)

    def comment(
        self, bound: Bound, reference: str, body: str, *, amends: str | None = None
    ) -> dict[str, Any] | StructuredError:
        refused = self._gated(bound, f"comment on work item {reference}")
        if refused is not None:
            return refused
        provider = bound.writer.provider_id
        try:
            proposal = bound.writer.propose_comment(
                ExternalId(provider=provider, value=reference),
                body,
                amends=None if amends is None else ExternalId(provider=provider, value=amends),
            )
        except Never4gaError as error:
            return _not_proposed(bound, error, {"work_item": reference})
        return self._outcome(bound, proposal, touching=reference)

    def relate(
        self,
        bound: Bound,
        reference: str,
        to: str,
        *,
        kind: str,
        description: str | None = None,
    ) -> dict[str, Any] | StructuredError:
        """Link two items on the tracker.

        Both references are the provider's own ids, so both are the provider's
        to recognise -- the adapter reads each end before drafting anything,
        which is what stops a typo becoming a relation to nothing.
        """
        refused = self._gated(bound, f"relate work item {reference} to {to}")
        if refused is not None:
            return refused
        provider = bound.writer.provider_id
        try:
            proposal = bound.writer.propose_relation(
                ExternalId(provider=provider, value=reference),
                ExternalId(provider=provider, value=to),
                kind=kind,
                description=description,
            )
        except Never4gaError as error:
            return _not_proposed(bound, error, {"work_item": reference, "relates_to": to})
        return self._outcome(bound, proposal, touching=reference)

    # -- the gates --------------------------------------------------------

    @staticmethod
    def _gated(bound: Bound, what: str) -> StructuredError | None:
        """The first gate: does this workspace permit writing at all?"""
        if bound.policy.disposition is not WriteDisposition.REFUSE:
            return None
        return StructuredError(
            "write_not_permitted",
            f"cannot {what}: workspace declares sync_policy "
            f"{bound.policy.sync_policy.strip()!r}, and writing needs 'read_write'",
            {
                "workspace": str(bound.workspace.concept_id),
                "sync_policy": bound.policy.sync_policy.strip(),
            },
            repair_hint="set `work_management.sync_policy: read_write` on the workspace, by hand",
        )

    def _outcome(
        self, bound: Bound, proposal: ProposedMutation, *, touching: str | None = None
    ) -> dict[str, Any] | StructuredError:
        """Apply, or report the draft. The second gate lives here."""
        payload: dict[str, Any] = {
            "workspace": str(bound.workspace.concept_id),
            "connection": bound.connection.name,
            "action": proposal.action.value,
            "proposal": proposal.render(),
            "applied": False,
        }
        if proposal.is_noop:
            # Worth stopping for: applying it would still bump a journal entry
            # on the tracker, saying somebody changed something when nobody did.
            payload["noop"] = True
            return payload
        if bound.policy.disposition is not WriteDisposition.APPLY:
            return payload
        try:
            result = bound.writer.apply(proposal)
        except Never4gaError as error:
            return StructuredError("write_failed", str(error), {"action": proposal.action.value})
        payload["applied"] = True
        self._forget(bound, touching or (result.item.ref.value if result.item else None))
        if result.item is not None:
            payload["item"] = {
                "ref": result.item.ref.value,
                "title": result.item.title,
                "status": result.item.status,
                "url": result.item.url,
            }
        if result.activity is not None:
            payload["activity"] = result.activity.value
        return payload

    def _forget(self, bound: Bound, reference: str | None) -> None:
        """Drop the cached copy of an item a write just changed.

        Forget rather than update. An applied mutation returns a fresh item for
        some actions and nothing at all for a comment, so a cache that
        sometimes held a half-item would be a subtler version of the stale
        answer this removes. Forgetting costs one refetch and is right for every action.

        The key comes from :func:`reference_key`, the same function the read
        path looks up with, because an invalidation that built its own key
        could forget a row nobody reads and leave the stale one answering.
        """
        if self._cache is None or not reference:
            return
        self._cache.forget(
            reference_key(
                bound.connection.name,
                bound.connection.project_ref or "",
                ExternalId(provider=bound.writer.provider_id, value=reference),
            )
        )


def _not_proposed(bound: Bound, error: Never4gaError, details: dict[str, Any]) -> StructuredError:
    """Why a draft could not be made, kept apart by what a caller should do next.

    Nothing has been sent when a proposal fails, so a tracker that did not
    answer is safe to ask again and says so. An item the tracker does not have
    is reported as the read side reports it, so a caller checks the reference
    rather than the values. Anything else -- a value the
    instance refuses, a write it does not permit -- is `propose_failed`. Only
    the first is worth retrying.
    """
    if isinstance(error, ProviderUnavailableError):
        return tracker_unreachable(bound.connection.name, error, details)
    if isinstance(error, WorkItemNotFoundError):
        return StructuredError(
            "work_item_not_found",
            str(error),
            {"connection": bound.connection.name, **details},
        )
    return StructuredError("propose_failed", str(error), details)
