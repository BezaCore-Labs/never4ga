"""The captured OpenProject API v3 responses, served to httpx.

`tests/fixtures/openproject/README.md` records where these came from and what
was sanitized. This module turns them into transports, so that every test of
the adapter runs against a real instance's answers without touching a network.

Two transports, deliberately different:

- :func:`fixture_transport` pages and filters the captured collection itself.
  It is a small fake server, and it is what the contract suite runs against.
- :func:`captured_pages_transport` serves the three files captured at
  ``pageSize=3`` and refuses every other request. It is the fixture that pins
  the behaviour most likely to be got wrong -- ``offset`` is a 1-based page
  number, so a paginator that added ``pageSize`` to it would ask for offset 4
  and be refused here while passing every single-page test.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, quote, urlparse

import httpx

__all__ = [
    "BASE_URL",
    "FIXTURES",
    "PROJECT_REF",
    "DownReader",
    "DownTracker",
    "TrackerReader",
    "TrackerWriter",
    "captured_pages_transport",
    "fixture_transport",
    "load_fixture",
    "offline_transport",
    "unauthorized_transport",
]

FIXTURES: Final = Path(__file__).parent / "fixtures" / "openproject"

#: The sanitized host. Nothing here reaches it; httpx still needs a base.
BASE_URL: Final = "https://openproject.example"

PROJECT_REF: Final = "never4ga"

#: The work package the captured single-item responses describe.
KNOWN_WORK_PACKAGE: Final = "838"

#: A second item the writable fake knows, because a relation needs two ends and
#: the far one has to be readable for the adapter to draft anything.
KNOWN_OTHER_WORK_PACKAGE: Final = "839"

#: OpenProject's identifier for a stale-version refusal.
_CONFLICT: Final = "urn:openproject-org:api:v3:errors:UpdateConflict"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _json(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _element_id(element: Any, link: str) -> str | None:
    href = (element["_links"].get(link) or {}).get("href")
    return None if href is None else str(href).rsplit("/", 1)[-1]


def _matches_filters(element: Any, filters: Sequence[Any]) -> bool:
    for entry in filters:
        for field, clause in entry.items():
            operator = clause.get("operator")
            values = [str(value) for value in clause.get("values", [])]
            if field == "status":
                if operator == "*":
                    continue
                if operator == "o":
                    # The instance's closed statuses; enough for the fake.
                    if _element_id(element, "status") in {"12", "14"}:
                        return False
                    continue
                if _element_id(element, "status") not in values:
                    return False
            elif field == "assignee":
                if _element_id(element, "assignee") not in values:
                    return False
            elif field == "search":
                subject = element["subject"].casefold()
                if not all(value.casefold() in subject for value in values):
                    return False
            elif field == "updatedAt":
                bound = values[0] if values else ""
                if bound and element["updatedAt"] < _normalised(bound):
                    return False
    return True


def _normalised(timestamp: str) -> str:
    # The captured payloads spell instants as `...Z`; a filter may send an
    # offset. Comparing them as text needs one spelling.
    parsed = datetime.fromisoformat(timestamp)
    return parsed.isoformat().replace("+00:00", "Z")


def fixture_transport(*, relations: str = "relations_empty.json") -> httpx.MockTransport:
    """A fake instance that pages and filters the captured collection."""
    collection = load_fixture("work-packages.json")
    elements = collection["_embedded"]["elements"]

    def handle(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        query = parse_qs(urlparse(str(request.url)).query)

        if path == "/api/v3":
            return _json(load_fixture("root.json"))
        if path == f"/api/v3/projects/{PROJECT_REF}":
            return _json(load_fixture("project.json"))
        if path == f"/api/v3/projects/{PROJECT_REF}/available_assignees":
            # Project-scoped on the real instance, and the same shape. The fake
            # serves the principals fixture so one file describes both.
            return _json(load_fixture("principals.json"))
        if path == f"/api/v3/projects/{PROJECT_REF}/versions":
            return _json(load_fixture("versions.json"))
        if path in {
            "/api/v3/statuses",
            "/api/v3/types",
            "/api/v3/priorities",
            "/api/v3/principals",
        }:
            return _json(load_fixture(f"{path.rsplit('/', 1)[-1]}.json"))
        if path == "/api/v3/relations":
            # The instance answers a nested relations path with a 308 to
            # this one, carrying the work package in a filter -- and an
            # unfiltered request is every relation there is, which on the
            # captured instance is none. Losing the filter is therefore
            # silent, and httpx does lose it if handed an empty params
            # mapping. Answering the two requests differently is what makes
            # `test_relations_are_read_for_one_item_not_for_a_search` catch it.
            if "filters" not in query:
                return _json(load_fixture("relations_empty.json"))
            return _json(load_fixture(relations))
        if path.endswith("/relations"):
            identifier = path.split("/")[-2]
            involved = json.dumps([{"involved": {"operator": "=", "values": [identifier]}}])
            return httpx.Response(
                308, headers={"Location": f"/api/v3/relations?filters={quote(involved)}"}
            )
        if path.startswith("/api/v3/work_packages/"):
            identifier = path.rsplit("/", 1)[-1]
            if identifier == KNOWN_WORK_PACKAGE:
                return _json(load_fixture("work-package.json"))
            return _json(load_fixture("not-found.json"), status=404)
        if path.endswith("/work_packages"):
            return _json(_page(elements, query, collection.get("_links", {})))
        return _json({"message": f"unrouted {path}"}, status=404)

    return httpx.MockTransport(handle)


def _page(
    elements: Sequence[Any],
    query: dict[str, list[str]],
    links: Mapping[str, Any] | None = None,
) -> Any:
    """One page of the captured collection, keeping the collection's own links.

    The links matter: OpenProject advertises `createWorkPackage` on the
    collection and omits it for a token that may not create one, so they are
    how the adapter discovers a write capability. A synthetic page without them
    would make every token look unable to create.
    """
    filters = json.loads(query.get("filters", ["[]"])[0])
    matching = [element for element in elements if _matches_filters(element, filters)]
    page_size = int(query.get("pageSize", ["20"])[0])
    offset = int(query.get("offset", ["1"])[0])
    start = (offset - 1) * page_size
    window = matching[start : start + page_size]
    return {
        "_type": "Collection",
        "total": len(matching),
        "count": len(window),
        "pageSize": page_size,
        "offset": offset,
        "_links": dict(links or {}),
        "_embedded": {"elements": window},
    }


def captured_pages_transport() -> httpx.MockTransport:
    """Serve only what was captured at ``pageSize=3``, and refuse anything else."""
    pages = {
        "1": load_fixture("work-packages_page-1.json"),
        "2": load_fixture("work-packages_page-2.json"),
        "3": load_fixture("work-packages_page-3.json"),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        page_size = query.get("pageSize", [""])[0]
        offset = query.get("offset", [""])[0]
        if page_size != "3" or offset not in pages:
            return _json(
                {"message": f"no capture for pageSize={page_size} offset={offset}"},
                status=400,
            )
        return _json(pages[offset])

    return httpx.MockTransport(handle)


def unauthorized_transport() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        return _json({"message": "You did not provide the correct credentials."}, status=401)

    return httpx.MockTransport(handle)


def offline_transport() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handle)


class TrackerWriter:
    """The write service, bound to a real OpenProject writer over ``transport``.

    What every interface says about a tracker's answer is driven through the
    real adapter and the real service -- only the network is a fake. ``bind``
    skips the vault, which the write tests cover on their own; everything after
    it is what runs in production.
    """

    CONNECTION: Final = "work_openproject"

    def __init__(self, transport: httpx.MockTransport | None = None) -> None:
        from never4ga.adapters.fakes import (
            FakeRepositoryLocator,
            InMemoryDocumentStore,
            InMemoryWorkspaceMappingStore,
        )
        from never4ga.services.work_writing import WorkWriteService
        from never4ga.services.workspaces import WorkspaceService

        self._service = WorkWriteService(
            documents=InMemoryDocumentStore(),
            workspaces=WorkspaceService(InMemoryWorkspaceMappingStore(), FakeRepositoryLocator([])),
            factory=lambda _: None,
        )
        self._transport = transport if transport is not None else writable_transport()

    def bind(self, where: Any = None, *, applying: bool = True) -> Any:
        from never4ga.adapters.openproject import OpenProjectWriter
        from never4ga.domain.connections import Connection
        from never4ga.domain.document import StoredDocument, VaultPath
        from never4ga.domain.identity import ConceptId
        from never4ga.domain.work_policy import WritePolicy
        from never4ga.services.work_writing import Bound

        workspace = ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbb6")
        return Bound(
            workspace=StoredDocument(
                concept_id=workspace,
                path=VaultPath.parse("10_Workspaces/Example/workspace.md"),
                frontmatter={
                    "type": "workspace",
                    "id": str(workspace),
                    "schema": "never4ga/0.1",
                    "title": "Example",
                    "created_at": "2026-08-26T00:00:00Z",
                },
                body="# Example\n",
            ),
            connection=Connection(name=self.CONNECTION, provider="openproject", base_url=BASE_URL),
            writer=OpenProjectWriter(
                base_url=BASE_URL,
                token="not-a-real-token",
                connection=self.CONNECTION,
                project_ref=PROJECT_REF,
                transport=self._transport,
            ),
            policy=WritePolicy(mode="external", sync_policy="read_write", applying=applying),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)


class DownTracker(TrackerWriter):
    """:class:`TrackerWriter` over an instance that is down."""

    def __init__(self) -> None:
        super().__init__(offline_transport())


class TrackerReader:
    """The read service, over the instance a :class:`TrackerWriter` on ``transport`` writes to."""

    def __init__(self, transport: httpx.MockTransport | None = None) -> None:
        from never4ga.adapters.fakes import (
            FakeRepositoryLocator,
            InMemoryDocumentStore,
            InMemoryWorkspaceMappingStore,
        )
        from never4ga.services.work_reading import WorkReadService
        from never4ga.services.workspaces import WorkspaceService

        self._service = WorkReadService(
            documents=InMemoryDocumentStore(),
            workspaces=WorkspaceService(InMemoryWorkspaceMappingStore(), FakeRepositoryLocator([])),
            factory=lambda _: None,
        )
        self._transport = transport if transport is not None else fixture_transport()

    def bind(self, where: Any = None) -> Any:
        from never4ga.services.work_reading import BoundReader

        bound = TrackerWriter(self._transport).bind()
        return BoundReader(
            workspace=bound.workspace,
            connection=bound.connection,
            provider=bound.writer,
            project=PROJECT_REF,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)


class DownReader(TrackerReader):
    """:class:`TrackerReader` over the instance :class:`DownTracker` cannot reach."""

    def __init__(self) -> None:
        super().__init__(offline_transport())


#: Field names the writable transport recognises, as OpenProject spells them.
#: Anything else is *ignored in silence*, as a live instance does, which is the
#: whole reason a write path verifies its own result.
WRITABLE_FIELDS: Final = ("subject", "description", "startDate", "dueDate", "percentageDone")

#: Link names a write may set here. A live instance's work package form carries
#: each of them as a writable link. A link missing from this list would make the
#: fake agree with an adapter that refuses it, rather than with the instance.
WRITABLE_LINKS: Final = ("status", "priority", "type", "parent", "assignee", "responsible")


def _link_titles() -> dict[str, str]:
    """Every status, priority and principal href on the captured instance."""
    titles: dict[str, str] = {}
    for collection in ("statuses", "priorities", "principals"):
        for element in load_fixture(f"{collection}.json")["_embedded"]["elements"]:
            titles[str(element["_links"]["self"]["href"])] = str(element["name"])
    return titles


#: OpenProject's relation pairs, as its `reverseType` names them.
_REVERSE_TYPES: dict[str, str] = {
    "relates": "relates",
    "duplicates": "duplicated",
    "blocks": "blocked",
    "follows": "precedes",
    "includes": "partof",
    "requires": "required",
}


def writable_transport() -> httpx.MockTransport:
    """A fake instance that can be changed, and misbehaves the way the real one does.

    Four behaviours of a live instance are modelled deliberately, because a write path
    that is not tested against them is not tested at all:

    - an unknown field is **ignored**, answering 200 with no error and no
      version bump;
    - a stale ``lockVersion`` answers **409 UpdateConflict**;
    - a ``PATCH`` answers with the **whole resource**, version already bumped;
    - the form answers **200 for an unknown name**, omitting it from the schema
      rather than reporting it, and reports a bad *value* in
      ``validationErrors``.
    """
    item = load_fixture("work-package.json")
    state: dict[str, Any] = {
        "item": item,
        "next_id": 900,
        "created": {},
        "next_activity": 1800,
        "activities": {},
        "projects": {},
    }

    def schema() -> dict[str, Any]:
        fields = {name: {"writable": True} for name in WRITABLE_FIELDS + WRITABLE_LINKS}
        fields["lockVersion"] = {"writable": True}
        return fields

    def create(body: Mapping[str, Any]) -> dict[str, Any]:
        """A new work package, with the defaults the instance fills in.

        `type` defaults to Task when a creation omits it, as on a live
        instance -- so a fake that refused without one would make a test
        pass that the real thing would fail, and the reverse.
        """
        state["next_id"] = int(state["next_id"]) + 1
        made = dict(load_fixture("work-package.json"))
        made["id"] = state["next_id"]
        made["displayId"] = str(state["next_id"])
        made["lockVersion"] = 0
        made["subject"] = body.get("subject", "")
        links = dict(made["_links"])
        links["type"] = {"href": "/api/v3/types/1", "title": "Task"}
        for name in WRITABLE_LINKS:
            wanted = (body.get("_links") or {}).get(name)
            if wanted:
                href = str(wanted["href"])
                links[name] = {"href": href, "title": _link_titles().get(href, "")}
        made["_links"] = links
        for name in WRITABLE_FIELDS:
            if name in body:
                made[name] = body[name]
        state["created"][str(made["id"])] = made
        return made

    def apply(body: Mapping[str, Any]) -> dict[str, Any]:
        current = dict(state["item"])
        for name in WRITABLE_FIELDS:
            if name in body:
                current[name] = body[name]
        links = dict(current["_links"])
        for name in WRITABLE_LINKS:
            wanted = (body.get("_links") or {}).get(name)
            if wanted:
                # The instance answers a link with its title beside the href,
                # and the normaliser reads the title. A fake that stored only
                # what was sent would make a written status read as absent.
                href = str(wanted["href"])
                links[name] = {"href": href, "title": _link_titles().get(href, "")}
        current["_links"] = links
        current["lockVersion"] = current["lockVersion"] + 1
        state["item"] = current
        return current

    def handle(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        identifier = str(state["item"]["id"])
        if request.method in {"POST", "PATCH"}:
            body = json.loads(request.content or b"{}")
            if path == f"/api/v3/projects/{PROJECT_REF}/work_packages/form":
                # The creation form needs no lockVersion -- posting {} answers
                # 200 where the same body against an existing work package
                # answers 409.
                errors = (
                    {"subject": {"message": "Subject can't be blank."}}
                    if body.get("subject") == ""
                    else {}
                )
                return _json(
                    {
                        "_type": "Form",
                        "_embedded": {
                            "payload": {k: v for k, v in body.items() if k in schema()},
                            "schema": schema(),
                            "validationErrors": errors,
                        },
                    }
                )
            if path == f"/api/v3/projects/{PROJECT_REF}/work_packages":
                return _json(create(body), status=201)
            if path == "/api/v3/projects" and request.method == "POST":
                slug = str(body.get("identifier", ""))
                if slug in state["projects"]:
                    return _json(
                        {"message": f"Identifier {slug} has already been taken."}, status=422
                    )
                parent = ((body.get("_links") or {}).get("parent") or {}).get("href")
                made = {
                    "_type": "Project",
                    "id": 9000 + len(state["projects"]),
                    "identifier": slug,
                    "name": body.get("name", slug),
                    "_links": {"parent": {"href": parent}} if parent else {},
                }
                state["projects"][slug] = made
                return _json(made, status=201)
            if path.endswith("/relations") and request.method == "POST":
                # Its own resource with its own collection, answering 201 with
                # the Relation rather than with either work package.
                near = {"href": f"/api/v3/work_packages/{path.split('/')[-2]}"}
                far = (body.get("_links") or {}).get("to", {})
                kind = body.get("type")
                # The instance stores "A precedes B" as `follows` from B to
                # A, and answers the POST with that.
                if kind == "precedes":
                    kind, reverse, near, far = "follows", "precedes", far, near
                else:
                    reverse = _REVERSE_TYPES.get(str(kind), kind)
                return _json(
                    {
                        "_type": "Relation",
                        "id": 99,
                        "type": kind,
                        "reverseType": reverse,
                        "description": body.get("description"),
                        "_links": {
                            "self": {"href": "/api/v3/relations/99"},
                            "from": near,
                            "to": far,
                        },
                    },
                    status=201,
                )
            if path.endswith("/activities") and request.method == "POST":
                # The instance takes the formattable object here and answers
                # 201 with the created Activity::Comment.
                said = (body.get("comment") or {}).get("raw")
                if said is None:
                    return _json({"message": "Bad request: comment is invalid"}, status=400)
                state["next_activity"] = int(state["next_activity"]) + 1
                made = {
                    "_type": "Activity::Comment",
                    "id": state["next_activity"],
                    "comment": {"format": "markdown", "raw": said, "html": f"<p>{said}</p>"},
                }
                state["activities"][str(made["id"])] = made
                return _json(made, status=201)
            if path.startswith("/api/v3/activities/") and request.method == "PATCH":
                made = state["activities"].get(path.rsplit("/", 1)[-1])
                if made is None:
                    return _json(load_fixture("not-found.json"), status=404)
                said = body.get("comment")
                if not isinstance(said, str):
                    # The instance's asymmetry: editing wants a bare string and
                    # refuses the object that created it, with a message that
                    # reads like something else entirely.
                    return _json({"message": "Bad request: comment is invalid"}, status=400)
                made["comment"] = {
                    "format": "markdown",
                    "raw": said,
                    "html": f"<p>{said}</p>",
                }
                return _json(made)
            if path == f"/api/v3/work_packages/{identifier}/form":
                if body.get("lockVersion") != state["item"]["lockVersion"]:
                    return _json(
                        {
                            "message": "Bad request: lockVersion",
                            "errorIdentifier": _CONFLICT,
                        },
                        status=409,
                    )
                errors = (
                    {"subject": {"message": "Subject can't be blank."}}
                    if body.get("subject") == ""
                    else {}
                )
                return _json(
                    {
                        "_type": "Form",
                        "_embedded": {
                            "payload": {k: v for k, v in body.items() if k in schema()},
                            "schema": schema(),
                            "validationErrors": errors,
                        },
                    }
                )
            if path == f"/api/v3/work_packages/{identifier}":
                if body.get("lockVersion") != state["item"]["lockVersion"]:
                    return _json(
                        {
                            "message": "Bad request: the resource has been updated.",
                            "errorIdentifier": _CONFLICT,
                        },
                        status=409,
                    )
                return _json(apply(body))
            return _json({"message": f"unrouted {request.method} {path}"}, status=404)

        if path == "/api/v3":
            return _json(load_fixture("root.json"))
        if path in {
            "/api/v3/statuses",
            "/api/v3/types",
            "/api/v3/priorities",
            "/api/v3/principals",
        }:
            return _json(load_fixture(f"{path.rsplit('/', 1)[-1]}.json"))
        if path == f"/api/v3/work_packages/{identifier}":
            return _json(state["item"])
        if path == f"/api/v3/work_packages/{KNOWN_OTHER_WORK_PACKAGE}":
            far = dict(load_fixture("work-package.json"))
            far["id"] = int(KNOWN_OTHER_WORK_PACKAGE)
            far["displayId"] = KNOWN_OTHER_WORK_PACKAGE
            return _json(far)
        if path.startswith("/api/v3/activities/"):
            made = state["activities"].get(path.rsplit("/", 1)[-1])
            return _json(made) if made else _json(load_fixture("not-found.json"), status=404)
        if path.startswith("/api/v3/work_packages/"):
            made = state["created"].get(path.rsplit("/", 1)[-1])
            if made is not None:
                return _json(made)
            return _json(load_fixture("not-found.json"), status=404)
        if path.endswith("/work_packages"):
            return _json(_page([state["item"]], {}, load_fixture("work-packages.json")["_links"]))
        if path == "/api/v3/relations":
            return _json(load_fixture("relations_empty.json"))
        if path == f"/api/v3/projects/{PROJECT_REF}/available_assignees":
            # Project-scoped on the real instance, and the same shape. The fake
            # serves the principals fixture so one file describes both.
            return _json(load_fixture("principals.json"))
        if path == f"/api/v3/projects/{PROJECT_REF}/versions":
            return _json(load_fixture("versions.json"))
        if path.startswith("/api/v3/projects/"):
            slug = path.rsplit("/", 1)[-1]
            if slug == PROJECT_REF:
                return _json(load_fixture("project.json"))
            made = state["projects"].get(slug)
            return _json(made) if made else _json(load_fixture("not-found.json"), status=404)
        return _json({"message": f"unrouted {path}"}, status=404)

    return httpx.MockTransport(handle)
