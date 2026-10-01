"""Linking a workspace to a tracker project, creating the project if need be.

Never4gA can create the tracker project when a workspace is created or mapped,
rather than requiring it to exist already. `core/03` section 23 and section 27
step 3 both assume the project is already there; this covers the case where it
is not.

**It is a standalone verb** that `workspace create` may also call, because most
workspaces already exist by the time anyone links them. A trigger that only
fired at creation would be unusable for those.

**The declaration is written with ``sync_policy: read``.** Section 15 says
"for the first implementation, `read` SHOULD precede `read_write`", and there is
a sharper reason: if this verb wrote `read_write`, the write gate would be
something the tool grants itself. Promoting a workspace is a deliberate edit a
person makes, which is exactly what makes it a gate.

**The ``sync_policy`` gate cannot apply here.** It gates writes to a tracker a
workspace already declares; this is the act that *establishes* the
declaration, so there is nothing to read a policy from. A separate permission
stands in its place.

**Creating a project needs its own permission.** This verb does two different
things depending on what is already there. Recording a mapping to a project
that exists is nearly harmless; *creating* one is the outward-facing act, and it
is the half that deserves the friction -- which is the same reasoning
``--apply`` itself rests on. So ``creating`` gates only that half. A migration
linking thirty workspaces to projects that already exist never asks for it; one
provisioning new projects declares that intent once.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final

from never4ga.domain.connections import Connection
from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import ConceptId
from never4ga.domain.work_policy import DEFAULT_POLICY, EXTERNAL_MODE, WORK_MANAGEMENT_KEY
from never4ga.errors import Never4gaError, WriteRejectedError
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.work_management import WorkManagementWriter, WorkProject
from never4ga.services.connections import ConnectionRegistry

__all__ = [
    "LinkOutcome",
    "ProposedLink",
    "WorkspaceLinkService",
    "project_identifier_for",
]

type WriterFactory = Callable[[Connection], WorkManagementWriter | None]

_SEPARATORS: Final = re.compile(r"[\s_]+")
_UNSAFE: Final = re.compile(r"[^a-z0-9-]+")
_RUNS: Final = re.compile(r"-{2,}")


def project_identifier_for(title: str) -> str:
    """A tracker project slug for a workspace title.

    Lowercase, because a project identifier is a slug: it appears in every URL
    and in the path work-package creation addresses. ``Never4gA`` becomes
    ``never4ga``.
    """
    slug = _RUNS.sub("-", _UNSAFE.sub("-", _SEPARATORS.sub("-", title.strip().casefold())))
    return slug.strip("-")


@dataclass(frozen=True, slots=True)
class ProposedLink:
    """What linking would do. Nothing has happened yet."""

    workspace: ConceptId
    workspace_title: str
    connection: str
    provider: str
    identifier: str
    name: str
    parent: str | None
    #: Whether the project has to be made, or is already there.
    creates_project: bool
    declaration: Mapping[str, Any]

    def render(self) -> str:
        lines = [f"link workspace {self.workspace_title} to {self.connection}"]
        if self.creates_project:
            lines.append(f"  create project   {self.identifier}  ({self.name})")
            if self.parent:
                lines.append(f"  under            {self.parent}")
        else:
            lines.append(f"  use project      {self.identifier}  (it already exists)")
        lines.append(f"  declare          sync_policy: {self.declaration['sync_policy']}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class LinkOutcome:
    """What linking did."""

    proposal: ProposedLink
    project: WorkProject
    created_project: bool
    document: StoredDocument | None = None
    #: Set when a project was made and the declaration could not be written.
    #: A project is not cheap to delete and this service will not pretend to
    #: have undone one, so the identifier is handed back to be dealt with.
    orphaned: str | None = None


class WorkspaceLinkService:
    """Establishes `core/03` section 15's declaration, making the project if needed."""

    def __init__(
        self,
        *,
        documents: DocumentStore,
        connections: ConnectionRegistry,
        factory: WriterFactory,
    ) -> None:
        self._documents = documents
        self._connections = connections
        self._factory = factory

    def propose(
        self,
        workspace: ConceptId,
        connection_name: str,
        *,
        identifier: str | None = None,
        name: str | None = None,
        parent: str | None = None,
    ) -> ProposedLink:
        """Work out what linking would do, and ask the tracker nothing else."""
        manifest = self._manifest(workspace)
        title = str(manifest.frontmatter.get("title", "")) or str(workspace)
        declared = manifest.frontmatter.get(WORK_MANAGEMENT_KEY)
        if isinstance(declared, Mapping) and str(declared.get("mode", "")).strip() == EXTERNAL_MODE:
            raise WriteRejectedError(
                f"workspace {title!r} already declares work management "
                f"({declared.get('connection')!r} -> {declared.get('project_ref')!r}); "
                "editing that is a person's change, not this verb's"
            )

        connection = self._connections.resolve(connection_name)
        writer = self._writer(connection)
        slug = (identifier or project_identifier_for(title)).strip()
        if not slug:
            raise WriteRejectedError(
                f"workspace {title!r} does not slug to a usable project identifier; name one"
            )
        existing = writer.find_project(slug)
        return ProposedLink(
            workspace=workspace,
            workspace_title=title,
            connection=connection.name,
            provider=connection.provider,
            identifier=slug,
            name=name or title,
            parent=parent if parent is not None else self._parent_project(manifest),
            creates_project=existing is None,
            declaration={
                "mode": EXTERNAL_MODE,
                "connection": connection.name,
                "provider": connection.provider,
                "project_ref": slug,
                # Never `read_write`: see this module's docstring.
                "sync_policy": DEFAULT_POLICY,
            },
        )

    def apply(self, proposal: ProposedLink, *, creating: bool = False) -> LinkOutcome:
        """Make the project if it is missing, then write the declaration.

        ``creating`` is the project-creation gate, and it stands in front of exactly one
        half of this verb: making something on somebody's instance. Linking to
        a project that is already there does not need it.

        The order has a consequence worth naming. If the declaration write
        fails after a project was made, a project exists that nothing
        references -- and a project is not cheap to delete. This reports exactly
        that, with the identifier, rather than attempting a rollback it cannot
        honestly perform.
        """
        connection = self._connections.resolve(proposal.connection)
        writer = self._writer(connection)
        existing = writer.find_project(proposal.identifier)
        if existing is not None:
            project, created = existing, False
        elif not creating:
            raise WriteRejectedError(
                f"no project {proposal.identifier!r} exists on {proposal.connection}, and "
                "creating one is a separate permission; pass it explicitly to make one"
            )
        else:
            project = writer.create_project(
                proposal.identifier, proposal.name, parent=proposal.parent
            )
            created = True

        try:
            document = self._declare(proposal)
        except Never4gaError as error:
            raise WriteRejectedError(
                f"the project {project.identifier!r} was "
                + ("created" if created else "found")
                + f", and the workspace declaration could not be written: {error}. "
                + (
                    f"Nothing references {project.identifier!r}; "
                    "delete it or link a workspace to it by hand."
                    if created
                    else "Nothing was created."
                )
            ) from error
        return LinkOutcome(
            proposal=proposal, project=project, created_project=created, document=document
        )

    # -- internals --------------------------------------------------------

    def _manifest(self, workspace: ConceptId) -> StoredDocument:
        manifest = self._documents.get(workspace)
        if manifest is None:
            raise WriteRejectedError(f"no workspace {workspace}")
        if manifest.frontmatter.get("type") != "workspace":
            raise WriteRejectedError(f"{workspace} is not a workspace")
        return manifest

    def _parent_project(self, manifest: StoredDocument) -> str | None:
        """The parent workspace's tracker project, when it has one.

        A child workspace's tracker project belongs under its parent's, which
        is the arrangement `core/03` section 28's worked example has.
        """
        parent = manifest.frontmatter.get("parent")
        if not parent:
            return None
        try:
            found = self._documents.get(ConceptId.parse(str(parent)))
        except Never4gaError:
            return None
        if found is None:
            return None
        declared = found.frontmatter.get(WORK_MANAGEMENT_KEY)
        if not isinstance(declared, Mapping):
            return None
        reference = str(declared.get("project_ref", "")).strip()
        return reference or None

    def _declare(self, proposal: ProposedLink) -> StoredDocument:
        manifest = self._manifest(proposal.workspace)
        frontmatter = dict(manifest.frontmatter)
        frontmatter[WORK_MANAGEMENT_KEY] = dict(proposal.declaration)
        updated = replace(manifest, frontmatter=frontmatter)
        self._documents.put(updated)
        return updated

    def _writer(self, connection: Connection) -> WorkManagementWriter:
        writer = self._factory(connection)
        if writer is None:
            raise WriteRejectedError(
                f"connection {connection.name!r} has no writable adapter on this machine; "
                "a token may be missing from the secret store"
            )
        return writer
