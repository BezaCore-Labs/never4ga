"""OpenProject as a :class:`WorkManagementProvider`.

`details/openproject-adapter.md` section 5's read set, and nothing beyond it.

Two behaviours are worth reading the code for, because both are contracts the
rest of Never4gA leans on.

**Degradation is reported, never disguised.** :meth:`OpenProjectProvider.health`
never raises -- an unreachable tracker answers ``available=False`` -- while a
read raises :class:`ProviderUnavailableError` rather than returning nothing. An
empty list from a work tracker means "no open work", and a caller must never be
handed that sentence because a laptop was on a train. `core/05` section 19,
and the port contract says the same.

**Capability is discovered, not declared** (section 12). What an instance can do
depends on its version, its configured modules and what the token is permitted
to read, so the adapter asks. The probes run once, on first use, and each one
that fails simply withholds its capability -- except when *none* of them
answered, which is a statement about the network rather than about the
instance, and is not remembered.

Reads only. No method here creates, updates or comments; :class:`OpenProjectWriter`
adds those.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Self

import httpx

from never4ga.adapters.openproject.api import (
    API_ROOT,
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEOUT,
    OpenProjectApi,
    encode_filters,
    work_package_filters,
)
from never4ga.adapters.openproject.normalise import PROVIDER_ID, work_item
from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.domain.connections import Connection
from never4ga.domain.identity import ExternalId
from never4ga.errors import ProviderUnavailableError, UnknownWorkItemStatusError
from never4ga.ports.work_management import ProviderHealth, WorkItem, WorkItemQuery

__all__ = ["OpenProjectProvider", "ProjectSummary"]

#: How many work packages a search reads when the caller sets no limit. A
#: personal tracker is small; a Context Pack is bounded regardless (core/07
#: section 9), and the signal provider that consumes this is bounded again.
DEFAULT_SEARCH_LIMIT: Final = 200

#: The capabilities that mean changing something. Named once so that filtering
#: them is a rule rather than a list repeated wherever it matters.
_WRITE_CAPABILITIES: Final = frozenset(
    {
        WorkManagementCapability.CREATE_WORK_ITEM,
        WorkManagementCapability.UPDATE_WORK_ITEM,
        WorkManagementCapability.COMMENT_WORK_ITEM,
    }
)


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    """A project as both of the names OpenProject accepts for it.

    A project href takes either the slug or the numeric id, but the *path* form
    of work-package creation wants the slug, so which one is in hand matters and
    both are kept. `core/03` section 17's
    ``project_ref`` is the slug.
    """

    identifier: str
    numeric_id: str
    name: str
    url: str


class OpenProjectProvider:
    """One project on one OpenProject instance. Reads only."""

    provider_id = PROVIDER_ID

    #: What *this class* can do, as opposed to what the instance permits.
    #: Discovery reads permissions off HAL action links, which say what the
    #: token may do -- a different question from whether any code here can do
    #: it. `capabilities` reports the intersection, so a reader never claims a
    #: write it has no method for. :class:`OpenProjectWriter` widens it with the
    #: writes it implements.
    _WRITABLE: frozenset[WorkManagementCapability] = frozenset()

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        connection: str,
        project_ref: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api = OpenProjectApi(base_url, token=token, timeout=timeout, transport=transport)
        self._connection = connection
        self._project_ref = project_ref
        self._capabilities: frozenset[WorkManagementCapability] | None = None
        #: Why the write half of the last discovery went unmeasured, when it
        #: was because the instance could not be asked. A writer reports this
        #: rather than "does not support", which is a different fact.
        self._unmeasured_because: ProviderUnavailableError | None = None
        self._statuses: dict[str, str] | None = None
        self._status_names: tuple[str, ...] = ()
        self._version: str | None = None
        self._asked_for_version = False

    @classmethod
    def from_connection(
        cls,
        connection: Connection,
        *,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> Self:
        """Join the vault's half of a connection to the secret store's half.

        `core/03` section 16 splits them by secrecy and joins them by name. The
        composition root resolves both; this is where they meet, and it is the
        only place either is needed.
        """
        if not connection.base_url:
            raise ProviderUnavailableError(
                f"connection {connection.name!r} defines no base_url, "
                "so there is no instance to read"
            )
        return cls(
            base_url=connection.base_url,
            token=token,
            connection=connection.name,
            project_ref=connection.project_ref,
            timeout=timeout,
            transport=transport,
        )

    def __repr__(self) -> str:
        return (
            f"OpenProjectProvider(connection={self._connection!r}, "
            f"project_ref={self._project_ref!r})"
        )

    def close(self) -> None:
        self._api.close()

    # -- the port ---------------------------------------------------------

    @property
    def capabilities(self) -> frozenset[WorkManagementCapability]:
        """What this instance, version and token can actually do (section 12).

        Discovered on first use and then remembered: a capability set is a
        property of a running instance, and asking once per process is the
        compromise between a hardcoded list, which would be wrong, and asking
        on every call, which would make a bounded read unbounded.

        A discovery that reached nothing is not remembered, so an instance that
        happened to be asleep is asked again rather than written off.
        """
        if self._capabilities is None:
            found, settled = self._discover_capabilities()
            discovered = self._implemented(found)
            if not discovered:
                # Nothing answered. An instance that was asleep the first time
                # it was asked must not be treated as incapable for the life of
                # the process, so this is not remembered.
                return frozenset()
            if not settled:
                # The write half is read off an existing work package, and this
                # project has none, so those capabilities were not measured --
                # they are unknown, not absent. Remembering that would make an
                # empty project permanently unable to update or comment for the
                # life of the process, long after the first item appeared.
                return discovered
            self._capabilities = discovered
        return self._capabilities

    def health(self) -> ProviderHealth:
        """Never raises. An unreachable instance says so."""
        try:
            root = self._api.get(API_ROOT)
        except ProviderUnavailableError as error:
            return ProviderHealth(available=False, detail=str(error))
        self._version = str(root.get("coreVersion", "")) or None
        self._asked_for_version = True
        detail = f"OpenProject {self._version}" if self._version else "OpenProject"
        return ProviderHealth(available=True, detail=f"{detail} at {self._api.base_url}")

    def get_work_item(self, ref: ExternalId, *, refresh: bool = False) -> WorkItem | None:
        """One work package, or ``None`` if this tracker has no such thing."""
        if ref.provider != self.provider_id:
            # An ExternalId is namespaced. Asking OpenProject for another
            # tracker's key is a caller's mistake, and the honest answer is
            # absence -- not a request that would 404 for the wrong reason.
            return None
        payload = self._api.get_optional(f"{API_ROOT}/work_packages/{ref.value}")
        if payload is None:
            return None
        return self._normalise(payload, relations=self._relations_for(ref.value))

    def search_work_items(self, query: WorkItemQuery) -> Sequence[WorkItem]:
        """Work packages matching a provider-neutral query.

        Raises rather than returning nothing when the instance cannot be
        reached: see the module docstring.
        """
        status_ids = self._resolve_statuses(query.statuses)
        limit = query.limit if query.limit is not None else DEFAULT_SEARCH_LIMIT
        if limit <= 0:
            # Asking for nothing is answered by nothing. Sending `pageSize=0`
            # would be a request whose only possible answer we already have.
            return ()
        filters = work_package_filters(
            query,
            status_ids=status_ids,
            own_project_only=bool(query.project or self._project_ref),
        )
        elements = self._api.collection(
            self._work_packages_path(query.project),
            {"filters": encode_filters(filters)},
            page_size=min(limit, DEFAULT_PAGE_SIZE),
            # An assignee filter is applied below rather than by the API, so
            # the read has to be wide enough to filter from -- but still
            # bounded. A client-side filter is not a licence to fetch a whole
            # tracker.
            limit=max(limit, DEFAULT_SEARCH_LIMIT) if query.assignees else limit,
        )
        # Relations are not read here: one request per result would turn a
        # bounded search into an unbounded one. `get_work_item` reads them.
        found = [self._normalise(element) for element in elements]
        if query.assignees:
            # Filtered here rather than by the API: see `work_package_filters`.
            wanted = {name.casefold() for name in query.assignees}
            found = [
                item
                for item in found
                if item.assignee is not None and item.assignee.casefold() in wanted
            ]
            found = found[:limit]
        return tuple(found)

    # -- beyond the port, still section 5 ---------------------------------

    def resolve_project(self, project_ref: str | None = None) -> ProjectSummary:
        """The project this provider reads (section 5, "resolve project")."""
        reference = project_ref or self._project_ref
        if not reference:
            raise ProviderUnavailableError(
                f"connection {self._connection!r} names no project to resolve"
            )
        payload = self._api.get(f"{API_ROOT}/projects/{reference}")
        identifier = str(payload.get("identifier", reference))
        return ProjectSummary(
            identifier=identifier,
            numeric_id=str(payload.get("id", "")),
            name=str(payload.get("name", identifier)),
            url=f"{self._api.base_url}/projects/{identifier}",
        )

    def work_item_url(self, ref: ExternalId) -> str:
        """The page a person can open (section 5, "build UI URL")."""
        return f"{self._api.base_url}/work_packages/{ref.value}"

    # -- internals --------------------------------------------------------

    def _work_packages_path(self, project_ref: str | None) -> str:
        reference = project_ref or self._project_ref
        if reference:
            return f"{API_ROOT}/projects/{reference}/work_packages"
        return f"{API_ROOT}/work_packages"

    def _normalise(
        self, payload: Mapping[str, Any], relations: Sequence[Mapping[str, Any]] = ()
    ) -> WorkItem:
        return work_item(
            payload=payload,
            connection=self._connection,
            project_ref=str(self._project_ref or ""),
            base_url=self._api.base_url,
            provider_version=self._provider_version(),
            relations=relations,
        )

    def _provider_version(self) -> str | None:
        """The instance's version, asked for once and never again.

        Read lazily rather than as a side effect of :meth:`health`, so that
        whether a work item carries section 8's ``provider_version`` does not
        depend on the order a caller happened to make its calls in.

        A version is a label. Failing a read because the root endpoint was slow
        would trade a whole answer for one, so an unreachable root costs the
        field and nothing else.
        """
        if not self._asked_for_version:
            self._asked_for_version = True
            try:
                root = self._api.get(API_ROOT)
            except ProviderUnavailableError:
                return None
            self._version = str(root.get("coreVersion", "")) or None
        return self._version

    def _relations_for(self, identifier: str) -> Sequence[Mapping[str, Any]]:
        if WorkManagementCapability.RELATIONS not in self.capabilities:
            return ()
        payload = self._api.get_optional(f"{API_ROOT}/work_packages/{identifier}/relations")
        if payload is None:
            return ()
        elements: Sequence[Mapping[str, Any]] = payload.get("_embedded", {}).get("elements", [])
        return elements

    def _resolve_statuses(self, names: Sequence[str]) -> tuple[str, ...]:
        """Status *names* as the ids the API filters on.

        An id typo silently targets the wrong status while a name typo cannot,
        which is why the port speaks names and the resolution happens here.
        """
        if not names:
            return ()
        if self._statuses is None:
            elements = [dict(element) for element in self._api.collection(f"{API_ROOT}/statuses")]
            self._statuses = {
                str(element["name"]).casefold(): str(element["id"]) for element in elements
            }
            # Kept as the instance spells them, so a refusal can name them back
            # to the person who mistyped one.
            self._status_names = tuple(sorted(str(element["name"]) for element in elements))
        unknown = tuple(name for name in names if name.casefold() not in self._statuses)
        if unknown:
            # Neither a dropped filter nor an empty result. A dropped filter
            # answers a narrow question with every ticket there is; an empty
            # result makes a typo indistinguishable from an empty backlog --
            # `--status open` on an instance whose statuses are New, Closed and
            # Rejected, say.
            raise UnknownWorkItemStatusError(unknown, self._status_names)
        return tuple(self._statuses[name.casefold()] for name in names)

    def _discover_capabilities(self) -> tuple[frozenset[WorkManagementCapability], bool]:
        """What was found, and whether the finding is settled enough to keep.

        Unsettled means the write half could not be measured: either the
        collection answered and was empty, so there was no work package whose
        links could say whether this token may update or comment, or the
        collection did not answer at all. Both are a different fact from "may
        not", and only a settled answer is worth remembering.
        """
        found: set[WorkManagementCapability] = set()
        settled = True
        self._unmeasured_because = None

        try:
            sample = self._api.get(self._work_packages_path(None), {"pageSize": 1})
        except ProviderUnavailableError as error:
            # Every write capability is read off this one answer, so without it
            # the whole write half is unknown rather than absent -- and must not
            # be remembered, even when another probe answered.
            sample = None
            settled = False
            self._unmeasured_because = error
        if sample is not None:
            found.add(WorkManagementCapability.READ_WORK_ITEMS)
            found.add(WorkManagementCapability.SEARCH_WORK_ITEMS)
            of_collection, had_element = self._capabilities_of(sample)
            found |= of_collection
            settled = had_element
        if self._probe(f"{API_ROOT}/relations", {"pageSize": 1}) is not None:
            found.add(WorkManagementCapability.RELATIONS)
        if (
            self._project_ref
            and self._probe(f"{API_ROOT}/projects/{self._project_ref}/versions", {"pageSize": 1})
            is not None
        ):
            # OpenProject models a sprint as a version; the Backlogs module is
            # what makes the distinction, and its absence is not an error.
            found.add(WorkManagementCapability.SPRINTS)
        return frozenset(found), settled

    def _probe(self, path: str, params: Mapping[str, Any]) -> Any | None:
        """A capability question. A refusal is an answer, not a failure."""
        try:
            return self._api.get(path, params)
        except ProviderUnavailableError:
            return None

    @staticmethod
    def _capabilities_of(
        collection: Mapping[str, Any],
    ) -> tuple[set[WorkManagementCapability], bool]:
        """What a collection and one of its work packages reveal.

        The second value is whether there was a work package to read at all.
        Without one the write half is unmeasured rather than refused, and the
        caller needs to tell those apart.

        The write half is read from HAL action links, because OpenProject omits
        the link for an action the authenticated user may not perform. That
        makes the links the instance's own answer to section 12's question --
        capability as a function of the token's permissions rather than of what
        this adapter was compiled to do. An admin token, for example, sees
        `update`, `updateImmediately`, `addComment` and `delete` on an item, and
        `createWorkPackage` on the collection.
        """
        found: set[WorkManagementCapability] = set()

        # Creation belongs to the collection: one work package cannot say
        # whether another may be made. The spelling difference is the API's --
        # a collection offers `createWorkPackageImmediate` where a project
        # offers `createWorkPackageImmediately`.
        collection_links = collection.get("_links", {})
        if {"createWorkPackage", "createWorkPackageImmediate"} & set(collection_links):
            found.add(WorkManagementCapability.CREATE_WORK_ITEM)

        elements = collection.get("_embedded", {}).get("elements", [])
        if not elements:
            return found, False
        element = elements[0]
        links = element.get("_links", {})
        if "parent" in links and "ancestors" in links:
            found.add(WorkManagementCapability.HIERARCHY)
        if any(key.startswith("customField") for key in element):
            # Presence of the settings link proves only that the feature exists
            # in the product. A field on an actual work package proves the
            # instance uses one, which is the question section 12 asks.
            found.add(WorkManagementCapability.CUSTOM_FIELDS)
        if "addRelation" in links:
            found.add(WorkManagementCapability.RELATE_WORK_ITEMS)
        if {"update", "updateImmediately"} & set(links):
            found.add(WorkManagementCapability.UPDATE_WORK_ITEM)
        if "addComment" in links:
            found.add(WorkManagementCapability.COMMENT_WORK_ITEM)
        return found, True

    def _implemented(
        self, found: frozenset[WorkManagementCapability]
    ) -> frozenset[WorkManagementCapability]:
        """Drop what the instance permits but this class cannot do."""
        return frozenset(
            capability
            for capability in found
            if capability in self._WRITABLE or capability not in _WRITE_CAPABILITIES
        )
