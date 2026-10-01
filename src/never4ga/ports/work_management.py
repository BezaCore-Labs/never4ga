"""WorkManagementProvider -- external operational work state.

A PM tracker stays the external system of record for configured operational
work. Never4gA reads it mechanically (core/07 Stage F) rather than asking a
model to infer ticket state, and does not mirror every ticket into Markdown.

The write half is a *second* Protocol rather than methods on the first:
core/03 section 24 says
"if a provider lacks a feature, the capability is reported unavailable", and a
provider that cannot write must not be made to implement writing to satisfy
structural typing. A writer is always also a reader, though -- it needs the
current version to propose against and the current state to verify with -- so
:class:`WorkManagementWriter` extends the reader rather than standing beside it.

**The value types live here rather than in ``domain``** for the same reason
:class:`WorkItem` does: they describe an interaction with a provider, not a
canonical concept. The workspace-side half of the write decision --
``sync_policy``, ``--apply`` -- is vault semantics and lives in
``domain/work_policy.py``.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.identity import ExternalId

__all__ = [
    "ProposedMutation",
    "ProviderHealth",
    "WorkItem",
    "WorkItemQuery",
    "WorkManagementProvider",
    "WorkManagementWriter",
    "WorkProject",
    "WriteAction",
    "WriteResult",
]

#: How an absent value reads in a rendered proposal. "None" would be a value.
UNSET = "(unset)"


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Whether an optional provider is usable right now (core/05 section 19)."""

    available: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class WorkItem:
    """A normalised external work item.

    ``ref`` is an :class:`ExternalId`, never a Never4gA identity. A work item may
    be *linked* to a canonical concept, but it is not one.
    """

    ref: ExternalId
    title: str
    status: str | None = None
    assignee: str | None = None
    priority: str | None = None
    milestone: str | None = None
    updated_at: datetime | None = None
    url: str | None = None
    relations: tuple[ExternalId, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))


@dataclass(frozen=True, slots=True)
class WorkItemQuery:
    """A provider-neutral work-item filter.

    Terms are stripped and blank ones dropped here rather than at each
    interface, because all three build one of these. A blank term would reach
    OpenProject, which rejects it with a 400 that would read as an unreachable
    tracker. A search for nothing is a search with no terms.
    """

    project: str | None = None
    statuses: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    updated_since: datetime | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        cleaned = tuple(term.strip() for term in self.terms if term.strip())
        if cleaned != self.terms:
            object.__setattr__(self, "terms", cleaned)


@runtime_checkable
class WorkManagementProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[WorkManagementCapability]: ...

    def get_work_item(self, ref: ExternalId, *, refresh: bool = False) -> WorkItem | None:
        """One work item.

        ``refresh`` asks for the tracker's answer rather than a cached one. A
        provider that holds no cache has nothing to bypass and ignores it,
        which is the truthful answer rather than an unsupported one.
        """
        ...

    def search_work_items(self, query: WorkItemQuery) -> Sequence[WorkItem]:
        """Raises ``ProviderUnavailableError`` when the provider is unreachable."""
        ...

    def health(self) -> ProviderHealth:
        """Never raises: an unreachable provider reports itself unavailable."""
        ...


class WriteAction(enum.StrEnum):
    """What a proposal would do.

    Four, because the provider does four different things: a field change is a
    ``PATCH`` against a version, a creation is a ``POST`` to a project, and a
    comment is its own request entirely: a ``comment`` key inside a ``PATCH``
    is dropped in silence. A relation is the fourth: it is its own resource
    with its own collection, not a field of either end, so it has no
    ``lockVersion`` to be computed against and cannot travel inside an update.
    """

    CREATE = "create"
    UPDATE = "update"
    COMMENT = "comment"
    RELATE = "relate"


@dataclass(frozen=True, slots=True)
class ProposedMutation:
    """A write that has not happened.

    core/03 section 22's draft state, as a value. It carries everything needed
    to send the write and everything needed to show a person what sending it
    would do -- including ``current``, so a proposal reads as "New -> Closed"
    rather than as "Closed" and a change to nothing is visible before a request
    is made.

    Build one through :meth:`update`, :meth:`create` or :meth:`comment`. The
    constructor is reachable, but those three are what enforce that a proposal
    carries what its action needs.
    """

    action: WriteAction
    #: What it targets. Absent for a creation: there is nothing there yet.
    ref: ExternalId | None = None
    #: Where a creation goes. Absent otherwise.
    project: str | None = None
    #: The values asked for, by normalised field name.
    fields: Mapping[str, Any] = field(default_factory=dict)
    #: Those same fields as the provider holds them now, so the draft can show
    #: what it would replace. Empty for a creation.
    current: Mapping[str, Any] = field(default_factory=dict)
    #: A comment's text. Not a field: a comment is not part of the resource.
    body: str | None = None
    #: An existing comment this one would replace, when there is one. What
    #: makes a repeated comment idempotent: the second write edits the first
    #: rather than adding another (`core/04` section 32).
    amends: ExternalId | None = None
    #: The version this was computed against (section 6). ``UPDATE`` only.
    lock_version: int | None = None
    #: The far end of a relation, and what kind it is. ``RELATE`` only. The
    #: near end is ``ref``, and the direction is the provider's business: what
    #: a caller states is that these two are related and how.
    relates_to: ExternalId | None = None
    relation_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "current", MappingProxyType(dict(self.current)))
        if self.action is WriteAction.UPDATE:
            if self.lock_version is None:
                raise ValueError(
                    "an update proposal needs the lock_version it was computed against; "
                    "the provider refuses a write without a fresh one"
                )
            if not self.fields:
                raise ValueError("an update proposal needs at least one field to change")
            if self.ref is None:
                raise ValueError("an update proposal needs the item it targets")
        elif self.action is WriteAction.CREATE:
            if not (self.project or "").strip():
                raise ValueError("a create proposal needs the project to create in")
            if not self.fields:
                raise ValueError("a create proposal needs at least one field")
        elif self.action is WriteAction.RELATE:
            if self.ref is None or self.relates_to is None:
                raise ValueError("a relation proposal needs both ends")
            if self.ref == self.relates_to:
                raise ValueError("an item cannot be related to itself")
            if not (self.relation_kind or "").strip():
                raise ValueError(
                    "a relation proposal needs the kind of relation; the provider has "
                    "no default and guessing one would assert something nobody said"
                )
        elif not (self.body or "").strip():
            raise ValueError("a comment proposal needs a body")
        elif self.ref is None:
            raise ValueError("a comment proposal needs the item it targets")

    # -- construction -----------------------------------------------------

    @classmethod
    def update(
        cls,
        ref: ExternalId,
        *,
        fields: Mapping[str, Any],
        current: Mapping[str, Any] | None = None,
        lock_version: int | None,
    ) -> ProposedMutation:
        return cls(
            action=WriteAction.UPDATE,
            ref=ref,
            fields=fields,
            current=current or {},
            lock_version=lock_version,
        )

    @classmethod
    def create(cls, project: str, *, fields: Mapping[str, Any]) -> ProposedMutation:
        return cls(action=WriteAction.CREATE, project=project, fields=fields)

    @classmethod
    def relate(
        cls,
        ref: ExternalId,
        to: ExternalId,
        *,
        kind: str,
        description: str | None = None,
    ) -> ProposedMutation:
        """Two items, and what connects them.

        ``description`` rides in ``body`` rather than earning a field of its
        own: it is free text a person wrote about this link, which is what
        ``body`` already means for a comment.
        """
        return cls(
            action=WriteAction.RELATE,
            ref=ref,
            relates_to=to,
            relation_kind=kind,
            body=description,
        )

    @classmethod
    def comment(
        cls, ref: ExternalId, body: str, *, amends: ExternalId | None = None
    ) -> ProposedMutation:
        return cls(action=WriteAction.COMMENT, ref=ref, body=body, amends=amends)

    # -- reading it -------------------------------------------------------

    @property
    def changes(self) -> Mapping[str, tuple[Any, Any]]:
        """Field name to ``(before, after)``, for the fields that would move."""
        return MappingProxyType(
            {
                name: (self.current.get(name), value)
                for name, value in self.fields.items()
                if self.current.get(name) != value
            }
        )

    @property
    def is_noop(self) -> bool:
        """Whether applying this would change nothing.

        Only an update can be one. A creation makes something that is not there
        and a comment adds something that was not said, so neither has an
        "already like that" to compare against.
        """
        return self.action is WriteAction.UPDATE and not self.changes

    def render(self) -> str:
        """What this would do, in the imperative, for a person to read.

        Pure: it makes no request and reads nothing but itself. Whether
        anything was actually sent is the caller's sentence to write, not this
        one's -- a draft that announced its own success would be the exact
        failure mode `--apply` exists to prevent.
        """
        if self.action is WriteAction.COMMENT:
            if self.amends is not None:
                return f"replace {_target(self.amends)} on {_target(self.ref)}\n  {self.body}"
            return f"comment on {_target(self.ref)}\n  {self.body}"
        if self.action is WriteAction.CREATE:
            lines = [f"create a work item in {self.project}"]
            lines += [
                f"  {name:<14} {_shown(value)}" for name, value in sorted(self.fields.items())
            ]
            return "\n".join(lines)
        lines = [f"update {_target(self.ref)}"]
        if self.is_noop:
            lines.append("  nothing would change")
        else:
            lines += [
                f"  {name:<14} {_shown(before)} -> {_shown(after)}"
                for name, (before, after) in sorted(self.changes.items())
            ]
        lines.append(f"  against lockVersion {self.lock_version}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What applying a proposal produced.

    ``item`` is the resource as the provider now holds it, which is what a
    write is verified against. ``activity`` identifies a comment that was posted, so a
    second write can edit the one it made rather than adding another.
    """

    proposal: ProposedMutation
    item: WorkItem | None = None
    activity: ExternalId | None = None


@dataclass(frozen=True, slots=True)
class WorkProject:
    """A provider's project, normalised to `core/03` section 24's minimum.

    ``identifier`` is the opaque string `core/03` section 15 calls
    ``project_ref``. It is the provider's, never Never4gA identity.
    """

    identifier: str
    name: str
    url: str | None = None
    parent: str | None = None


@runtime_checkable
class WorkManagementWriter(WorkManagementProvider, Protocol):
    """A provider that can also change what it reads.

    Every ``propose_*`` raises :class:`CapabilityNotSupportedError` when the
    provider does not permit that write, and :meth:`apply` raises it too: a
    proposal is a value and can travel, so the gate cannot live only where one
    is produced.
    """

    def propose_update(self, ref: ExternalId, fields: Mapping[str, Any]) -> ProposedMutation:
        """Read current state and draft the change. Sends nothing.

        Raises ``WorkItemNotFoundError`` when the tracker has no such item.
        """
        ...

    def propose_create(self, project: str, fields: Mapping[str, Any]) -> ProposedMutation: ...

    def propose_comment(
        self, ref: ExternalId, body: str, *, amends: ExternalId | None = None
    ) -> ProposedMutation:
        """Draft a comment, or a replacement for one already made.

        ``amends`` is what lets a caller comment twice and leave one comment:
        it names the activity a previous write produced, and the provider edits
        that rather than adding another.
        """
        ...

    def propose_relation(
        self, ref: ExternalId, to: ExternalId, *, kind: str, description: str | None = None
    ) -> ProposedMutation:
        """Draft a link between two items.

        Both ends are read first, so an id nobody has fails here rather than
        being posted -- the guarantee every other linked value already makes.
        """
        ...

    def apply(self, proposal: ProposedMutation) -> WriteResult:
        """Send it, and hand back what the provider says is true afterwards.

        Raises ``WriteConflictError`` when the proposal's version has been
        overtaken. Never retries: a retry with a fresh version is the silent
        overwrite core/03 section 22 forbids.
        """
        ...

    def find_project(self, identifier: str) -> WorkProject | None:
        """The provider's project of that name, or ``None`` if there is none."""
        ...

    def create_project(
        self, identifier: str, name: str, *, parent: str | None = None
    ) -> WorkProject:
        """Make a project on the provider.

        **Deliberately not capability-gated**, unlike every other write here.
        A capability must be discoverable from the instance -- section 12 makes
        it a function of version, modules and the token's permissions -- and
        OpenProject offers no oracle for this one: its projects collection
        carries no `createProject` action link even for an administrator.
        Claiming a capability nobody can check would be a
        statement about the product rather than about the instance, which is
        the thing that vocabulary exists to avoid. The gate is core/03 section
        22's write policy, and the provider's own refusal behind it.
        """
        ...


def _target(ref: ExternalId | None) -> str:
    return "an unnamed item" if ref is None else f"{ref.provider}:{ref.value}"


def _shown(value: Any) -> str:
    return UNSET if value is None else str(value)
