"""An OpenProject work package, normalised to `details/openproject-adapter.md` section 8.

Pure functions of a payload: nothing here makes a request, so what the adapter
does with a response can be tested against a captured one.

Two rules shape the result.

**Unavailable fields are omitted, never guessed** (section 8). A work package
with no assignee produces a :class:`WorkItem` with ``assignee=None`` and no
``assignee`` key in ``extra`` -- not an empty string, which a caller would
render as though somebody had cleared the field.

**A provider id is never canonical identity** (`core/06` section 3). Every
reference that leaves here is an :class:`ExternalId`, and `core/03` section 17's
other two terms -- the connection and the project -- travel with it, because
``838`` identifies nothing on its own.

The port's :class:`WorkItem` names the handful of fields every provider has.
Section 8's remainder lives in ``extra``, which is a read-only mapping: adding
provider fields must not mean growing the port every time a tracker has an idea.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Final

from never4ga.domain.identity import ExternalId
from never4ga.ports.work_management import WorkItem

__all__ = ["PROVIDER_ID", "work_item"]

#: The namespace every reference this adapter produces is qualified by.
PROVIDER_ID: Final = "openproject"

#: A description is context, not content: the canonical text stays in the
#: tracker. This is long enough to say what a ticket is about and short enough
#: that a Context Pack of them stays bounded (core/07 section 9).
EXCERPT_LIMIT: Final = 240

_CUSTOM_FIELD: Final = re.compile(r"^customField\d+$")
_WHITESPACE: Final = re.compile(r"\s+")


def work_item(
    *,
    payload: Mapping[str, Any],
    connection: str,
    project_ref: str,
    base_url: str,
    provider_version: str | None = None,
    relations: Sequence[Mapping[str, Any]] = (),
) -> WorkItem:
    """One work package as a normalised :class:`WorkItem`."""
    links = payload.get("_links", {})
    identifier = str(payload["id"])
    parent = _reference(links, "parent")

    extra: dict[str, Any] = {
        "connection": connection,
        "project_ref": project_ref,
        "display_id": str(payload.get("displayId", identifier)),
    }
    # `project_ref` is the project that was asked; this is the one the item
    # is in, which differs for an item read by id from another project.
    _set(extra, "project", _title(links, "project"))
    _set(extra, "type", _title(links, "type"))
    _set(extra, "responsible", _title(links, "responsible"))
    _set(extra, "category", _title(links, "category"))
    _set(extra, "sprint", _title(links, "sprint"))
    _set(extra, "description", _raw(payload.get("description")))
    _set(extra, "description_excerpt", _excerpt(payload.get("description")))
    _set(extra, "created_at", _instant(payload.get("createdAt")))
    _set(extra, "start_date", _day(payload.get("startDate")))
    _set(extra, "due_date", _day(payload.get("dueDate")))
    _set(extra, "percent_complete", payload.get("percentageDone"))
    _set(extra, "lock_version", payload.get("lockVersion"))
    _set(extra, "provider_version", provider_version)
    _set(extra, "parent", parent)

    related, kinds = _relations(identifier, relations)
    if parent is not None:
        # Hierarchy is a relation. Naming it in both places costs nothing and
        # means a caller asking "what else is this attached to" is not silently
        # missing the one relation almost every tracker has.
        related = (parent, *related)
        kinds = {parent.value: "parent", **kinds}
    if kinds:
        extra["relation_kinds"] = kinds

    extensions = {key: value for key, value in payload.items() if _CUSTOM_FIELD.match(key)}
    if extensions:
        extra["extensions"] = extensions

    return WorkItem(
        ref=ExternalId(provider=PROVIDER_ID, value=identifier),
        title=str(payload.get("subject", "")),
        status=_title(links, "status"),
        assignee=_title(links, "assignee"),
        priority=_title(links, "priority"),
        milestone=_title(links, "version"),
        updated_at=_instant(payload.get("updatedAt")),
        url=f"{base_url.rstrip('/')}/work_packages/{identifier}",
        relations=related,
        extra=extra,
    )


def _set(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _link(links: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    link = links.get(name)
    if not isinstance(link, Mapping) or link.get("href") is None:
        return None
    return link


def _title(links: Mapping[str, Any], name: str) -> str | None:
    link = _link(links, name)
    if link is None:
        return None
    title = link.get("title")
    return None if title is None else str(title)


def _reference(links: Mapping[str, Any], name: str) -> ExternalId | None:
    link = _link(links, name)
    if link is None:
        return None
    return _from_href(str(link["href"]))


def _from_href(href: str) -> ExternalId:
    return ExternalId(provider=PROVIDER_ID, value=href.rstrip("/").rsplit("/", 1)[-1])


def _relations(
    identifier: str, elements: Sequence[Mapping[str, Any]]
) -> tuple[tuple[ExternalId, ...], dict[str, str]]:
    """The other end of each relation, and what kind it is from this item's end.

    A relation names both ends, and one of them is the work package being
    normalised. Keeping it would make every item related to itself.

    OpenProject names a relation from its `from` end, so an item at the `to`
    end reads the relation's `reverseType`: A `precedes` B even though the
    instance stores `follows` from B to A. Taking `type` from either end would
    make every link in a chain read the same, and a predecessor could not be
    told from a successor.
    """
    found: list[ExternalId] = []
    kinds: dict[str, str] = {}
    for element in elements:
        links = element.get("_links", {})
        named = str(element.get("type", element.get("name", "relates")))
        target = _link(links, "to")
        if target is not None and _from_href(str(target["href"])).value == identifier:
            named = str(element.get("reverseType") or named)
        ends = [
            _from_href(str(link["href"]))
            for name in ("from", "to")
            if (link := _link(links, name)) is not None
        ]
        for end in ends:
            if end.value == identifier or end in found:
                continue
            found.append(end)
            kinds[end.value] = named
    return tuple(found), kinds


def _raw(description: Any) -> str | None:
    """The description as it was written.

    The excerpt beside it is what a *list* carries -- a search of fifty items
    must not pull fifty full descriptions. `work get` fetches one item on
    purpose, and truncating there would defeat the verb.
    """
    if not isinstance(description, Mapping):
        return None
    raw = description.get("raw")
    return None if not raw else str(raw)


def _excerpt(description: Any) -> str | None:
    if not isinstance(description, Mapping):
        return None
    raw = description.get("raw")
    if not raw:
        return None
    collapsed = _WHITESPACE.sub(" ", str(raw)).strip()
    if len(collapsed) <= EXCERPT_LIMIT:
        return collapsed
    return collapsed[: EXCERPT_LIMIT - 1].rstrip() + "…"


def _instant(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _day(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))
