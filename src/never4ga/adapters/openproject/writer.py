"""The OpenProject half of :class:`WorkManagementWriter`.

A subclass of the reader rather than a wrapper around it, because a writer
genuinely is one: it needs the current version to propose against and the
current state to verify with. Keeping the read-only class available matters
too -- a composition root that only reads gets something that structurally
cannot write, which is what makes the two-Protocol split in
`ports/work_management` real rather than decorative.

**Every write is verified before it is reported as done.** `details/openproject-adapter.md`
section 6 asks for current-state reads and provider validation, and that is not
enough on its own. OpenProject ignores an
unknown field rather than rejecting it -- 200, no error, no version bump -- so a
write path that trusted a status line could not tell a typo from success. Three
things stand between a caller and that:

1. **names are checked against the instance's own schema** before anything is
   sent, because the form reports an unknown name by *omission* rather than by
   error, and a client-side check is the only place that omission becomes a
   sentence somebody reads;
2. **values are validated by the form**, which does refuse a bad one;
3. **what came back is compared against what was asked for**, and a requested
   field absent from the answer counts as divergence.

A `409` becomes :class:`WriteConflictError` and nothing retries. Refetching the
version and sending again is the silent overwrite `core/03` section 22 forbids.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from never4ga.adapters.openproject.api import API_ROOT
from never4ga.adapters.openproject.provider import OpenProjectProvider
from never4ga.adapters.openproject.write import (
    LINKED_FIELDS,
    divergences,
    unknown_field_names,
    write_payload,
)
from never4ga.domain.capabilities import WorkManagementCapability, require_capability
from never4ga.domain.identity import ExternalId
from never4ga.errors import (
    CapabilityNotSupportedError,
    ProviderUnavailableError,
    WorkItemNotFoundError,
    WriteRejectedError,
)
from never4ga.ports.work_management import (
    ProposedMutation,
    WorkItem,
    WorkProject,
    WriteAction,
    WriteResult,
)

__all__ = ["OpenProjectWriter"]

#: Which endpoint resolves each linked field's name to an href.
_LINK_COLLECTIONS: Final[Mapping[str, str]] = {
    "status": "statuses",
    "priority": "priorities",
    "type": "types",
    # `responsible` reads the instance-wide collection because there is no
    # project-scoped one: `available_responsibles` answers 404 on 17.6.0, where
    # `available_assignees` exists. `assignee` therefore resolves against the
    # project's own list -- see :meth:`_link_collection` -- which is the
    # collection OpenProject fills its own picker from, so a name that
    # resolves is a name the instance will accept.
    "responsible": "principals",
}

#: Project-scoped, so it cannot live in the flat table above. Resolving an
#: assignee anywhere wider offers people the project would then refuse: a user
#: found in `principals` but not assignable here is answered with "The chosen
#: user is not allowed to be 'Assignee' for this work package."
_PROJECT_LINK_COLLECTIONS: Final[Mapping[str, str]] = {"assignee": "available_assignees"}

#: Stands in the link table for a name more than one element answers to, so a
#: lookup can tell "nobody" from "which one" without a second pass.
_AMBIGUOUS: Final = "?ambiguous"


class OpenProjectWriter(OpenProjectProvider):
    """One project on one OpenProject instance, readable and writable."""

    #: What this class can do, so that `capabilities` never names something
    #: with no method behind it -- an
    #: instance permitting a write is a different fact from this adapter being
    #: able to make one.
    _WRITABLE = frozenset(
        {
            WorkManagementCapability.CREATE_WORK_ITEM,
            WorkManagementCapability.UPDATE_WORK_ITEM,
            WorkManagementCapability.COMMENT_WORK_ITEM,
            WorkManagementCapability.RELATE_WORK_ITEMS,
        }
    )

    def __init__(self, **arguments: Any) -> None:
        super().__init__(**arguments)
        self._links: dict[str, dict[str, str]] = {}

    def __repr__(self) -> str:
        return super().__repr__().replace("OpenProjectProvider(", "OpenProjectWriter(", 1)

    def _require(self, needed: WorkManagementCapability) -> None:
        """Refuse a write this instance does not permit -- or could not be asked about.

        An instance that did not answer has not said no. Reporting it as
        :class:`CapabilityNotSupportedError` would tell a caller to stop when it
        should wait, so the reason discovery came back short is raised instead,
        and a refusal stays a refusal.
        """
        found = self.capabilities
        if needed not in found and self._unmeasured_because is not None:
            raise ProviderUnavailableError(
                str(self._unmeasured_because)
            ) from self._unmeasured_because
        require_capability(self.provider_id, found, needed)

    def _item(self, ref: ExternalId, needed: WorkManagementCapability) -> Mapping[str, Any]:
        """Read a work package, and refuse a write its own links do not offer.

        A write that names a work package is judged by that work package.
        OpenProject sends every one with its action links and omits the link
        for an action this token may not perform on it, so the item is the
        instance's exact answer -- where :attr:`capabilities` is read off one
        sampled item from the connection's own project, which says nothing
        about an item in another project and nothing at all when that project
        has no open item to sample.

        An instance that does not answer raises as unreachable from the read
        itself, so an outage is never reported as a refusal.
        """
        payload: Mapping[str, Any] | None = self._api.get_optional(
            f"{API_ROOT}/work_packages/{ref.value}"
        )
        if payload is None:
            raise WorkItemNotFoundError(f"{self.provider_id} has no work item {ref.value!r}")
        if not set(_ITEM_LINKS[needed]) & set(payload.get("_links", {})):
            raise CapabilityNotSupportedError(
                f"{self.provider_id} does not support {needed.value!r} on work item "
                f"{ref.value}: the instance offers this token no such action there"
            )
        return payload

    # -- proposing --------------------------------------------------------

    def propose_update(self, ref: ExternalId, fields: Mapping[str, Any]) -> ProposedMutation:
        """Read the item, validate the change against the instance, draft it.

        Two requests and no write: one ``GET`` for the current state and the
        version, one form ``POST`` for the schema and the provider's own opinion
        of the values. Both happen here rather than at apply time because this
        is the moment a person is shown what would happen, and a draft of
        something that cannot succeed is worse than no draft.
        """
        payload = self._item(ref, WorkManagementCapability.UPDATE_WORK_ITEM)
        lock_version = int(payload["lockVersion"])
        body = self._body(fields, lock_version=lock_version)
        self._check_against_form(f"{API_ROOT}/work_packages/{ref.value}/form", body)
        item = self._normalise(payload)
        return ProposedMutation.update(
            ref,
            fields=fields,
            current={name: _current(item, name) for name in fields},
            lock_version=lock_version,
        )

    def propose_create(self, project: str, fields: Mapping[str, Any]) -> ProposedMutation:
        """Validate a new work item against the project, and draft it.

        One request and no write: the project's creation form, which answers
        the same two questions the update form does -- it *reports* a bad value
        and says nothing about an unknown *name* beyond leaving it out of the
        schema.

        Unlike the update form this one needs no ``lockVersion``: posting ``{}``
        to it answers 200 where the same body against an existing work package
        answers 409. There is no version to be stale about yet. An omitted
        ``type`` is defaulted by the instance rather than refused.
        """
        self._require(WorkManagementCapability.CREATE_WORK_ITEM)
        reference = project or self._project_ref or ""
        body = self._body(fields, lock_version=None)
        self._check_against_form(self._creation_path(reference, form=True), body)
        return ProposedMutation.create(reference, fields=fields)

    def propose_comment(
        self, ref: ExternalId, body: str, *, amends: ExternalId | None = None
    ) -> ProposedMutation:
        """Draft a comment, or a replacement for one already made.

        A comment has no form to validate against and no version to be stale
        about, so this confirms the work package exists and drafts. There is
        nothing to check names against because a comment has one field, and it
        is not a field of the resource at all.
        """
        self._item(ref, WorkManagementCapability.COMMENT_WORK_ITEM)
        return ProposedMutation.comment(ref, body, amends=amends)

    def propose_relation(
        self, ref: ExternalId, to: ExternalId, *, kind: str, description: str | None = None
    ) -> ProposedMutation:
        """Read both ends, then draft the link.

        Both, because a relation names two items and either can be an id
        nobody has. The API answers 422 for a bad `to` and says little about
        which end was wrong, so the same rule the other linked fields follow
        applies: what cannot be resolved is refused, never sent.

        The kind is not validated here. OpenProject's vocabulary is fixed
        (`relates`, `duplicates`, `blocks`, `precedes`, `includes`, `requires`
        and their reverses) but it is the instance's, not this adapter's, and
        the form check at apply time is what reports a bad one -- in the
        instance's own words rather than in a list this adapter would have to
        keep current.
        """
        self._item(ref, WorkManagementCapability.RELATE_WORK_ITEMS)
        if self._api.get_optional(f"{API_ROOT}/work_packages/{to.value}") is None:
            raise WorkItemNotFoundError(f"{self.provider_id} has no work item {to.value!r}")
        return ProposedMutation.relate(ref, to, kind=kind, description=description)

    # -- applying ---------------------------------------------------------

    def apply(self, proposal: ProposedMutation) -> WriteResult:
        """Send the proposal, and verify what came back against what was asked."""
        if proposal.action is WriteAction.CREATE:
            # Creation is the collection's to permit: no item exists to ask.
            self._require(_NEEDED[proposal.action])
        else:
            assert proposal.ref is not None  # the value type guarantees it
            self._item(proposal.ref, _NEEDED[proposal.action])
        if proposal.action is WriteAction.COMMENT:
            return self._comment(proposal)
        if proposal.action is WriteAction.RELATE:
            return self._relate(proposal)
        body = self._body(proposal.fields, lock_version=proposal.lock_version)
        if proposal.action is WriteAction.CREATE:
            answered = self._api.post(
                self._creation_path(str(proposal.project or ""), form=False), body
            )
            named = f"a new work item in {proposal.project}"
        else:
            assert proposal.ref is not None  # the value type guarantees it
            answered = self._api.patch(f"{API_ROOT}/work_packages/{proposal.ref.value}", body)
            named = f"work item {proposal.ref.value}"
        diverged = divergences(body, answered)
        if diverged:
            raise WriteRejectedError(
                f"{self.provider_id} answered without making the change asked for to "
                f"{named}: " + "; ".join(diverged)
            )
        return WriteResult(proposal=proposal, item=self._normalise(answered))

    def _relate(self, proposal: ProposedMutation) -> WriteResult:
        """POST the relation, then read the near end back.

        A relation is its own resource, so what comes back is the relation
        rather than either work package -- and `WriteResult.item` is supposed
        to be the item as the provider now holds it. So the near end is read
        again after the write, which is also what proves the link is there:
        the POST answering 201 says a request succeeded, and the work package
        carrying the relation says the thing asked for happened.
        """
        assert proposal.ref is not None and proposal.relates_to is not None
        body: dict[str, Any] = {
            "_links": {"to": {"href": f"{API_ROOT}/work_packages/{proposal.relates_to.value}"}},
            "type": proposal.relation_kind,
        }
        if proposal.body:
            body["description"] = proposal.body
        answered = self._api.post(f"{API_ROOT}/work_packages/{proposal.ref.value}/relations", body)
        wanted = str(proposal.relation_kind)
        asked_from, asked_to = str(proposal.ref.value), str(proposal.relates_to.value)
        if not _is_the_relation_asked_for(answered, wanted, asked_from, asked_to):
            source, target = _relation_ends(answered)
            raise WriteRejectedError(
                f"{self.provider_id} made a {str(answered.get('type', ''))!r} relation "
                f"from {source or '?'} to {target or '?'} where {wanted!r} was asked for "
                f"from {asked_from} to {asked_to}"
            )
        near = self._api.get_optional(f"{API_ROOT}/work_packages/{proposal.ref.value}")
        if near is None:
            raise WriteRejectedError(
                f"{self.provider_id} accepted the relation and then had no work item "
                f"{proposal.ref.value!r} to show for it"
            )
        return WriteResult(proposal=proposal, item=self._normalise(near))

    def _comment(self, proposal: ProposedMutation) -> WriteResult:
        """Post a comment, or edit the one this caller made before.

        **The two disagree about the shape of the same field**, pinned by a
        test so nobody tidies it away:

        - ``POST /work_packages/{id}/activities`` wants the formattable object,
          ``{"comment": {"raw": ...}}``, and answers ``201``;
        - ``PATCH /activities/{id}`` wants a **bare string**, and refuses that
          same object with ``400 Bad request: comment is invalid`` -- a message
          that reads like a permission or content problem and is neither.

        A ``GET`` on the activity hands back the object form, so echoing what
        was just read is precisely what fails. The ``PATCH`` accepts no
        formattable variant.
        """
        assert proposal.ref is not None  # the value type guarantees it
        asked = str(proposal.body)
        if proposal.amends is not None:
            answered = self._api.patch(
                f"{API_ROOT}/activities/{proposal.amends.value}", {"comment": asked}
            )
        else:
            answered = self._api.post(
                f"{API_ROOT}/work_packages/{proposal.ref.value}/activities",
                {"comment": {"raw": asked}},
            )
        said = str((answered.get("comment") or {}).get("raw", ""))
        if said != asked:
            raise WriteRejectedError(
                f"{self.provider_id} accepted a comment on work item {proposal.ref.value} "
                f"that does not say what was asked: wanted {asked!r}, got {said!r}"
            )
        return WriteResult(
            proposal=proposal,
            activity=ExternalId(provider=self.provider_id, value=str(answered["id"])),
        )

    def _creation_path(self, project: str, *, form: bool) -> str:
        """Where a work package is made, or asked about.

        The project is addressed by its **slug** here, and this is the one
        place the API's relaxation about project references does not reach: a
        numeric id is accepted wherever a project href appears in a body, and
        not in this path segment.
        """
        if not project:
            raise WriteRejectedError(
                "cannot create a work item without a project; this connection names none"
            )
        suffix = "/form" if form else ""
        return f"{API_ROOT}/projects/{project}/work_packages{suffix}"

    # -- projects ---------------------------------------------------------

    def find_project(self, identifier: str) -> WorkProject | None:
        payload = self._api.get_optional(f"{API_ROOT}/projects/{identifier}")
        return None if payload is None else self._project(payload)

    def create_project(
        self, identifier: str, name: str, *, parent: str | None = None
    ) -> WorkProject:
        """``POST /api/v3/projects``. There is no `op-query` verb for it.

        The parent travels as a link, where a project href takes either the
        slug or the numeric id -- the relaxation that does *not* extend to the
        path segment work-package creation uses.
        """
        body: dict[str, Any] = {"identifier": identifier, "name": name}
        if parent:
            body["_links"] = {"parent": {"href": f"{API_ROOT}/projects/{parent}"}}
        answered = self._api.post(f"{API_ROOT}/projects", body)
        made = self._project(answered)
        if made.identifier != identifier:
            raise WriteRejectedError(
                f"{self.provider_id} made a project called {made.identifier!r} where "
                f"{identifier!r} was asked for"
            )
        return made

    def _project(self, payload: Mapping[str, Any]) -> WorkProject:
        identifier = str(payload.get("identifier", ""))
        parent = (payload.get("_links", {}).get("parent") or {}).get("href")
        return WorkProject(
            identifier=identifier,
            name=str(payload.get("name", identifier)),
            url=f"{self._api.base_url}/projects/{identifier}",
            parent=None if not parent else str(parent).rsplit("/", 1)[-1],
        )

    # -- internals --------------------------------------------------------

    def _body(self, fields: Mapping[str, Any], *, lock_version: int | None) -> dict[str, Any]:
        wanted = {name: fields[name] for name in fields if name in LINKED_FIELDS}
        return write_payload(fields, lock_version=lock_version, links=self._resolve_links(wanted))

    def _resolve_links(self, wanted: Mapping[str, Any]) -> dict[str, str]:
        """Linked field names to hrefs, resolved against the instance.

        Resolution is by name on purpose for status, priority and type: a name
        that does not exist fails to resolve and says so, where an id that does
        not exist silently targets something else. Each collection is read once
        per process. ``parent`` is the exception and is resolved by reading the
        item -- see :meth:`_parent_href`.
        """
        resolved: dict[str, str] = {}
        for name, value in wanted.items():
            if name == "parent":
                href = self._parent_href(value)
                if href is not None:
                    resolved[name] = href
                continue
            table = self._link_table(name)
            if not table and name in _PROJECT_LINK_COLLECTIONS:
                raise WriteRejectedError(
                    f"cannot write {name!r}: no user is assignable in "
                    f"{self._project_ref!r}. Somebody has to be a member of the project "
                    "with a role that allows it before anything can be assigned"
                )
            href = table.get(str(value).casefold())
            if href == _AMBIGUOUS:
                raise WriteRejectedError(
                    f"cannot write {name!r}: more than one of them is called "
                    f"{str(value)!r} here, so which was meant is not something this "
                    "can decide. Name one of them differently, or say who by id"
                )
            if href is not None:
                resolved[name] = href
        return resolved

    def _parent_href(self, value: Any) -> str | None:
        """A parent work package id as an href, or None if no such item exists.

        The other linked fields translate a name into an id. A parent is already
        an id, so there is nothing to translate and the resolution step earns its
        place a different way: it reads the item. An id nobody has fails here
        rather than being posted, which is what the API does with it otherwise --
        it answers 422 with a message about the parent, and only sometimes.
        """
        text = str(value).strip()
        if not text:
            # Detaching, handled in write_payload. Nothing to resolve.
            return None
        payload = self._api.get_optional(f"{API_ROOT}/work_packages/{text}")
        if payload is None:
            return None
        href = payload.get("_links", {}).get("self", {}).get("href")
        return str(href) if href else None

    def _link_collection(self, name: str) -> str:
        """Where the names for a linked field come from.

        Project-scoped where the API offers it, because an assignee that is
        not a member of the project is one the instance will refuse after the
        adapter has already said it resolved.
        """
        scoped = _PROJECT_LINK_COLLECTIONS.get(name)
        if scoped is not None:
            return f"{API_ROOT}/projects/{self._project_ref}/{scoped}"
        return f"{API_ROOT}/{_LINK_COLLECTIONS[name]}"

    def _link_table(self, name: str) -> Mapping[str, str]:
        if name not in self._links:
            elements = self._api.collection(self._link_collection(name))
            table: dict[str, str] = {}
            for element in elements:
                href = element.get("_links", {}).get("self", {}).get("href")
                if not href:
                    continue
                key = str(element["name"]).casefold()
                # A status name is unique by design; a person's is not, and
                # `principals` is the whole instance rather than this project.
                # Keeping the last one seen would assign work to whoever the
                # collection happened to list second, which is the
                # silently-wrong-target failure resolving by name exists to
                # prevent. So a shared name resolves to nothing and says why.
                table[key] = _AMBIGUOUS if key in table else str(href)
            self._links[name] = table
        return self._links[name]

    def _check_against_form(self, path: str, body: Mapping[str, Any]) -> None:
        """Ask the instance what it would accept, and refuse what it would not.

        The form needs the same fresh ``lockVersion`` a write does -- posting
        ``{}`` to it answers ``409``, not a validation error -- so the body is
        sent whole. What comes back is used two ways, because the endpoint
        answers two different questions with two different degrees of candour:
        it *reports* a bad value in ``validationErrors``, and it says nothing at
        all about an unknown *name*, merely leaving it out of the schema.

        A form that cannot be read is not a reason to refuse a write. The
        response comparison in :meth:`apply` is the check that cannot be
        skipped; this one is the check that makes the failure legible early.
        """
        try:
            form = self._api.post(path, body)
        except ProviderUnavailableError:
            return
        embedded = form.get("_embedded", {})
        unknown = unknown_field_names(body, embedded.get("schema", {}))
        if unknown:
            raise WriteRejectedError(
                f"{self.provider_id} has no field called {', '.join(repr(n) for n in unknown)} "
                "here; it would accept the write and ignore it"
            )
        invalid = embedded.get("validationErrors", {})
        if invalid:
            raise WriteRejectedError(
                f"{self.provider_id} would refuse this change: {_reasons(invalid)}"
            )


#: Which capability each action needs. A proposal is a value and can travel, so
#: applying is gated as well as producing one.
#: The action links a work package carries when this token may make each
#: write to it. The same names discovery reads off its sample.
_ITEM_LINKS: Final[Mapping[WorkManagementCapability, tuple[str, ...]]] = {
    WorkManagementCapability.UPDATE_WORK_ITEM: ("update", "updateImmediately"),
    WorkManagementCapability.COMMENT_WORK_ITEM: ("addComment",),
    WorkManagementCapability.RELATE_WORK_ITEMS: ("addRelation",),
}

_NEEDED: Final[Mapping[WriteAction, WorkManagementCapability]] = {
    WriteAction.CREATE: WorkManagementCapability.CREATE_WORK_ITEM,
    WriteAction.UPDATE: WorkManagementCapability.UPDATE_WORK_ITEM,
    WriteAction.COMMENT: WorkManagementCapability.COMMENT_WORK_ITEM,
    WriteAction.RELATE: WorkManagementCapability.RELATE_WORK_ITEMS,
}


def _current(item: WorkItem, name: str) -> Any:
    """A normalised field's present value, wherever the shape keeps it."""
    if hasattr(item, name):
        return getattr(item, name)
    return item.extra.get(name)


def _reasons(invalid: Mapping[str, Any]) -> str:
    parts: Sequence[str] = [
        f"{name}: {(detail or {}).get('message', 'refused')}" for name, detail in invalid.items()
    ]
    return "; ".join(parts)


def _relation_ends(answered: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The work packages a Relation runs from and to, by id."""
    links = answered.get("_links") or {}

    def end(name: str) -> str | None:
        href = (links.get(name) or {}).get("href")
        return None if not href else str(href).rstrip("/").rsplit("/", 1)[-1]

    return end("from"), end("to")


def _is_the_relation_asked_for(
    answered: Mapping[str, Any], wanted: str, near: str, far: str
) -> bool:
    """Whether the Relation that came back is the link that was asked for.

    OpenProject names one relation from one end, and does not always keep the
    end it was asked from: "A precedes B" is stored, and answered, as `follows`
    from B to A. Comparing the type alone would report that correct write as a
    failure, and would accept the asked-for type pointing the wrong way. So the
    ends are compared too, and the reverse pair is accepted only with its ends
    reversed. `reverseType` is
    the instance's own answer to what the relation is called from the far end,
    so no list of pairs is kept here to disagree with it.
    """
    ends = _relation_ends(answered)
    if answered.get("type") == wanted and ends == (near, far):
        return True
    return answered.get("reverseType") == wanted and ends == (far, near)
