"""`never4ga work update|create|comment` and `never4ga workspace link`.

Thin, deliberately. The gate logic is `services/work_writing.py`, because
`core/05` section 13 makes every interface a client over the same services and
`/v1/work` needs the identical behaviour. What lives here is what only a
composition root may do: pick the adapter.

`workspace link` stays a CLI verb and gets no HTTP twin. The API has no
workspace-creation surface for it to belong to -- `/v1/workspaces` reads and
resolves and does not create -- so an endpoint for this would mean inventing a
capability group to hold a single verb.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any

from never4ga.composition import work_read_service, writer_factory
from never4ga.composition import work_write_service as _work_write_service
from never4ga.domain.scope import ScopeRequest
from never4ga.errors import (
    Never4gaError,
    RepositoryMarkerError,
    ScopeResolutionError,
    StructuredError,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.secret_store import SecretStore
from never4ga.services.connections import ConnectionRegistry
from never4ga.services.work_linking import WorkspaceLinkService
from never4ga.services.work_writing import WorkWriteService
from never4ga.services.workspaces import WorkspaceService, scope_refusal

__all__ = ["work_read_service", "work_write_service", "workspace_link"]


def work_write_service(
    documents: DocumentStore,
    workspaces: WorkspaceService,
    secrets: SecretStore,
    trackers_database: Path,
) -> WorkWriteService:
    """The composition root's assembly, which all three roots share."""
    return _work_write_service(documents, workspaces, secrets, trackers_database)


def workspace_link(
    documents: DocumentStore,
    workspaces: WorkspaceService,
    secrets: SecretStore,
    where: PurePath,
    connection_name: str,
    *,
    identifier: str | None,
    apply: bool,
    create: bool = False,
) -> dict[str, Any] | StructuredError:
    """Link the workspace here to a tracker project, making it if needed.

    **The ``sync_policy`` gate cannot stand in front of this.** ``sync_policy``
    governs writes to a tracker a workspace already declares, and this is the
    act that establishes the declaration. So ``--apply`` covers the vault write,
    and ``--create`` covers the half that reaches somebody's instance: making a
    project that is not there.
    """
    try:
        resolution = workspaces.resolve(ScopeRequest(cwd=where))
    except (ScopeResolutionError, RepositoryMarkerError) as error:
        return scope_refusal(error, where)

    service = WorkspaceLinkService(
        documents=documents,
        connections=ConnectionRegistry(documents=documents),
        factory=writer_factory(secrets),
    )
    try:
        proposal = service.propose(
            resolution.scope.workspace_id, connection_name, identifier=identifier
        )
    except Never4gaError as error:
        return StructuredError("link_refused", str(error), {"connection": connection_name})

    payload: dict[str, Any] = {
        "workspace": str(resolution.scope.workspace_id),
        "connection": connection_name,
        "project_ref": proposal.identifier,
        "creates_project": proposal.creates_project,
        "proposal": proposal.render(),
        "applied": False,
    }
    payload["creating_allowed"] = create
    if proposal.creates_project and not create:
        # Said at proposal time rather than only on apply, so the requirement is
        # visible in the dry run that a person reads before deciding.
        payload["blocked"] = (
            f"no project {proposal.identifier!r} exists yet; pass --create to make one"
        )
    if not apply:
        return payload
    try:
        outcome = service.apply(proposal, creating=create)
    except Never4gaError as error:
        return StructuredError("link_failed", str(error), {"connection": connection_name})
    payload["applied"] = True
    payload["created_project"] = outcome.created_project
    payload["url"] = outcome.project.url
    return payload
