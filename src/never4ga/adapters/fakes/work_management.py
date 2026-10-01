"""In-memory WorkManagementProvider and WorkManagementWriter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from never4ga.domain.capabilities import WorkManagementCapability, require_capability
from never4ga.domain.identity import ExternalId
from never4ga.errors import (
    ProviderUnavailableError,
    WorkItemNotFoundError,
    WriteConflictError,
    WriteRejectedError,
)
from never4ga.ports.work_management import (
    ProposedMutation,
    ProviderHealth,
    WorkItem,
    WorkItemQuery,
    WorkProject,
    WriteAction,
    WriteResult,
)

__all__ = ["FakeWorkManagementProvider", "FakeWorkManagementWriter"]


class FakeWorkManagementProvider:
    """A work-management provider backed by a dictionary.

    ``available=False`` models the offline case of core/05 section 19: health
    still answers, reads fail explicitly, and the caller decides how to degrade.
    """

    provider_id = "fake-pm"

    def __init__(self, items: Sequence[WorkItem] = (), *, available: bool = True) -> None:
        self._items = {item.ref: item for item in items}
        self._available = available

    @property
    def capabilities(self) -> frozenset[WorkManagementCapability]:
        return frozenset(
            {
                WorkManagementCapability.READ_WORK_ITEMS,
                WorkManagementCapability.SEARCH_WORK_ITEMS,
            }
        )

    def _require_available(self) -> None:
        if not self._available:
            raise ProviderUnavailableError(f"{self.provider_id} is unreachable")

    def get_work_item(self, ref: ExternalId, *, refresh: bool = False) -> WorkItem | None:
        self._require_available()
        return self._items.get(ref)

    def search_work_items(self, query: WorkItemQuery) -> Sequence[WorkItem]:
        self._require_available()
        matches = [item for item in self._items.values() if _matches(item, query)]
        matches.sort(key=lambda item: item.ref.value)
        if query.limit is not None:
            matches = matches[: query.limit]
        return tuple(matches)

    def health(self) -> ProviderHealth:
        if self._available:
            return ProviderHealth(available=True)
        return ProviderHealth(available=False, detail=f"{self.provider_id} is unreachable")


def _matches(item: WorkItem, query: WorkItemQuery) -> bool:
    if query.statuses and item.status not in query.statuses:
        return False
    if query.assignees and item.assignee not in query.assignees:
        return False
    if query.updated_since and (item.updated_at is None or item.updated_at < query.updated_since):
        return False
    haystack = item.title.casefold()
    return all(term.casefold() in haystack for term in query.terms)


class FakeWorkManagementWriter(FakeWorkManagementProvider):
    """An in-memory writer, with the version behaviour that makes writes safe.

    Kept a separate class from the reader rather than a flag on it, so that a
    plain :class:`FakeWorkManagementProvider` stays something that structurally
    is *not* a ``WorkManagementWriter``. The two-Protocol split is only real if
    something demonstrates a provider that cannot write.

    ``writable=False`` models the other half of core/03 section 25: a tracker
    the token has read access to and nothing more. It refuses rather than
    quietly doing nothing.
    """

    provider_id = "fake-pm"

    def __init__(
        self,
        items: Sequence[WorkItem] = (),
        *,
        available: bool = True,
        writable: bool = True,
    ) -> None:
        super().__init__(items, available=available)
        self._writable = writable
        self._versions: dict[ExternalId, int] = dict.fromkeys(self._items, 1)
        self._next_id = 1
        self._activities = 0
        self._comments: dict[ExternalId, str] = {}
        self._projects: dict[str, WorkProject] = {}

    @property
    def capabilities(self) -> frozenset[WorkManagementCapability]:
        found = set(super().capabilities)
        if self._writable:
            found |= {
                WorkManagementCapability.CREATE_WORK_ITEM,
                WorkManagementCapability.UPDATE_WORK_ITEM,
                WorkManagementCapability.COMMENT_WORK_ITEM,
                WorkManagementCapability.RELATE_WORK_ITEMS,
            }
        return frozenset(found)

    # -- proposing --------------------------------------------------------

    def propose_update(self, ref: ExternalId, fields: Mapping[str, Any]) -> ProposedMutation:
        require_capability(
            self.provider_id, self.capabilities, WorkManagementCapability.UPDATE_WORK_ITEM
        )
        self._require_available()
        item = self._items.get(ref)
        if item is None:
            raise WorkItemNotFoundError(f"{self.provider_id} has no work item {ref.value!r}")
        return ProposedMutation.update(
            ref,
            fields=fields,
            current={name: getattr(item, name, None) for name in fields},
            lock_version=self._versions[ref],
        )

    def propose_create(self, project: str, fields: Mapping[str, Any]) -> ProposedMutation:
        require_capability(
            self.provider_id, self.capabilities, WorkManagementCapability.CREATE_WORK_ITEM
        )
        self._require_available()
        return ProposedMutation.create(project, fields=fields)

    def propose_comment(
        self, ref: ExternalId, body: str, *, amends: ExternalId | None = None
    ) -> ProposedMutation:
        require_capability(
            self.provider_id, self.capabilities, WorkManagementCapability.COMMENT_WORK_ITEM
        )
        self._require_available()
        if ref not in self._items:
            raise WorkItemNotFoundError(f"{self.provider_id} has no work item {ref.value!r}")
        return ProposedMutation.comment(ref, body, amends=amends)

    def propose_relation(
        self, ref: ExternalId, to: ExternalId, *, kind: str, description: str | None = None
    ) -> ProposedMutation:
        require_capability(
            self.provider_id, self.capabilities, WorkManagementCapability.RELATE_WORK_ITEMS
        )
        self._require_available()
        for end in (ref, to):
            if end not in self._items:
                raise WorkItemNotFoundError(f"{self.provider_id} has no work item {end.value!r}")
        return ProposedMutation.relate(ref, to, kind=kind, description=description)

    # -- applying ---------------------------------------------------------

    def find_project(self, identifier: str) -> WorkProject | None:
        self._require_available()
        return self._projects.get(identifier)

    def create_project(
        self, identifier: str, name: str, *, parent: str | None = None
    ) -> WorkProject:
        self._require_available()
        if identifier in self._projects:
            raise WriteRejectedError(f"{self.provider_id} already has a project {identifier!r}")
        made = WorkProject(
            identifier=identifier,
            name=name,
            url=f"https://fake-pm.invalid/projects/{identifier}",
            parent=parent,
        )
        self._projects[identifier] = made
        return made

    def comment_text(self, activity: ExternalId) -> str | None:
        """What a comment this writer made now says. Beyond the port."""
        return self._comments.get(activity)

    def _relate(self, proposal: ProposedMutation) -> WriteResult:
        """Record the link on both ends, and hand back the near one.

        Both ends, because a relation is symmetric in the only sense this fake
        models: whichever end somebody reads, the link is there. The real
        provider stores one resource and reports it from both, which is the
        same fact with fewer rows.
        """
        assert proposal.ref is not None and proposal.relates_to is not None
        for near, far in ((proposal.ref, proposal.relates_to), (proposal.relates_to, proposal.ref)):
            item = self._items[near]
            if far not in item.relations:
                self._items[near] = replace(item, relations=(*item.relations, far))
        return WriteResult(proposal=proposal, item=self._items[proposal.ref])

    def apply(self, proposal: ProposedMutation) -> WriteResult:
        needed = {
            WriteAction.CREATE: WorkManagementCapability.CREATE_WORK_ITEM,
            WriteAction.UPDATE: WorkManagementCapability.UPDATE_WORK_ITEM,
            WriteAction.COMMENT: WorkManagementCapability.COMMENT_WORK_ITEM,
            WriteAction.RELATE: WorkManagementCapability.RELATE_WORK_ITEMS,
        }[proposal.action]
        require_capability(self.provider_id, self.capabilities, needed)
        self._require_available()
        if proposal.action is WriteAction.CREATE:
            return self._create(proposal)
        if proposal.action is WriteAction.RELATE:
            return self._relate(proposal)
        if proposal.action is WriteAction.COMMENT:
            if proposal.amends is not None:
                if proposal.amends not in self._comments:
                    raise WorkItemNotFoundError(
                        f"{self.provider_id} has no comment {proposal.amends.value!r}"
                    )
                self._comments[proposal.amends] = str(proposal.body)
                return WriteResult(proposal=proposal, activity=proposal.amends)
            self._activities += 1
            made = ExternalId(provider=self.provider_id, value=f"a{self._activities}")
            self._comments[made] = str(proposal.body)
            return WriteResult(proposal=proposal, activity=made)
        return self._update(proposal)

    def _create(self, proposal: ProposedMutation) -> WriteResult:
        self._next_id += 1
        ref = ExternalId(provider=self.provider_id, value=f"new-{self._next_id}")
        item = WorkItem(ref=ref, title=str(proposal.fields.get("title", "")))
        item = _with_fields(item, proposal.fields)
        self._items[ref] = item
        self._versions[ref] = 1
        return WriteResult(proposal=proposal, item=item)

    def _update(self, proposal: ProposedMutation) -> WriteResult:
        assert proposal.ref is not None  # the value type guarantees it
        ref = proposal.ref
        item = self._items.get(ref)
        if item is None:
            raise WorkItemNotFoundError(f"{self.provider_id} has no work item {ref.value!r}")
        if self._versions[ref] != proposal.lock_version:
            raise WriteConflictError(
                f"{self.provider_id} work item {ref.value} has moved on: the proposal was "
                f"computed against version {proposal.lock_version} and it is now "
                f"{self._versions[ref]}"
            )
        updated = _with_fields(item, proposal.fields)
        self._items[ref] = updated
        self._versions[ref] += 1
        return WriteResult(proposal=proposal, item=updated)


def _with_fields(item: WorkItem, fields: Mapping[str, Any]) -> WorkItem:
    """Apply normalised field names to a work item.

    Anything the normalised shape does not have is kept in ``extra`` rather
    than dropped: core/02's tolerance rule is that unknown data survives, and a
    fake that silently lost a field would make a contract pass that a real
    adapter should fail.
    """
    known = {name: value for name, value in fields.items() if hasattr(item, name)}
    unknown = {name: value for name, value in fields.items() if not hasattr(item, name)}
    return replace(item, **known, extra={**item.extra, **unknown})
