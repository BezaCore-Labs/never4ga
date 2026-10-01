"""A normalised change, as an OpenProject body -- and whether it took.

Pure functions of a payload, like `normalise`: nothing here makes a request, so
every hazard of the live API is testable against a captured response. Three
behaviours of OpenProject's API shape this module:

- **an unknown field in a ``PATCH`` is ignored, not rejected.** 200, no error,
  no journal entry, no version bump. A typo in a field name is indistinguishable
  from success, which is why :func:`divergences` exists at all.
- **the form does not reject an unknown name either** -- it omits it from the
  echoed payload, and lists every real field in ``_embedded.schema``. So the
  schema is an *oracle* consulted here rather than a *gate* the server enforces,
  which is what :func:`unknown_field_names` is.
- **a ``PATCH`` response is the whole resource**, identical field for field to a
  fresh ``GET``. That makes comparing against the response a real read-back and
  saves a request. :func:`divergences` still treats a requested field that is
  *absent* from the response as divergence rather than as "unchanged", so the
  check stays correct if some later version answers with less.

Writing is deliberately narrower than reading. A field this adapter cannot
translate is refused by name rather than passed through, because passing it
through means the API ignores it in silence and the caller is told it worked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from never4ga.errors import WriteRejectedError

__all__ = [
    "LINKED_FIELDS",
    "WRITABLE_FIELDS",
    "divergences",
    "unknown_field_names",
    "write_payload",
]

#: Normalised name to the provider's spelling, for fields that travel as plain
#: values. `title` is what `details/openproject-adapter.md` section 8 calls it
#: and `subject` is what OpenProject does.
PLAIN_FIELDS: Final[Mapping[str, str]] = {
    "title": "subject",
    "start_date": "startDate",
    "due_date": "dueDate",
    "percent_complete": "percentageDone",
}

#: Fields that travel as a formattable object rather than a string.
FORMATTABLE_FIELDS: Final = frozenset({"description"})

#: Fields referenced by href. The caller resolves each name to a link, because
#: resolving needs a request and this module makes none. Resolving at the
#: boundary also makes an id typo impossible: a name that does not exist fails
#: to resolve, where an id that does not exist silently targets something else.
#:
#: `type` is a link on creation and on change, and the instance defaults it
#: (typically to Task) when a creation omits it. `assignee` and `responsible`
#: resolve like `status`: `GET /api/v3/principals` answers with a name and a
#: self href, as `statuses`, `priorities` and `types` do.
#:
#: `parent` resolves differently. A status or a type has a name that is unique
#: within the instance, so the caller looks the name up. A work package has no
#: such name -- the value IS an id -- so resolution verifies that the id exists
#: rather than translating anything. Same guarantee either way: what cannot be
#: resolved is refused, never sent.
LINKED_FIELDS: Final = frozenset(
    {"status", "priority", "type", "parent", "assignee", "responsible"}
)

WRITABLE_FIELDS: Final = frozenset(PLAIN_FIELDS) | FORMATTABLE_FIELDS | LINKED_FIELDS

#: Not a field of the resource: a `comment` key inside a PATCH returns 200 and
#: is dropped in silence. Named here so the refusal can say what to do instead.
_NOT_A_FIELD: Final[Mapping[str, str]] = {
    "comment": "a comment is its own request, not a field of the work package",
}

#: Sent on every write and expected to come back changed, so comparing it would
#: report a divergence on every successful write.
_NOT_COMPARED: Final = frozenset({"lockVersion"})


def _is_detach(value: Any) -> bool:
    """Whether a ``parent`` value means "no parent" rather than an id.

    ``None`` and the empty string, which is all the CLI can spell: ``--field
    parent=`` has no way to say null otherwise. Nothing else an id could be is
    ambiguous with this, and the proposal shows the change before it is sent.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def write_payload(
    fields: Mapping[str, Any],
    *,
    lock_version: int | None,
    links: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A normalised change as an OpenProject request body.

    ``links`` carries the already-resolved href for each of
    :data:`LINKED_FIELDS` being written. A linked field without one is refused
    rather than sent as a name, which the API would ignore without saying so.
    """
    resolved = links or {}
    body: dict[str, Any] = {}
    linked: dict[str, Any] = {}
    for name, value in fields.items():
        if name in _NOT_A_FIELD:
            raise WriteRejectedError(f"cannot write {name!r}: {_NOT_A_FIELD[name]}")
        if name == "parent" and _is_detach(value):
            # The one linked field with a meaningful empty value. A status or a
            # type is always something; a parent is optional, and `{"href":
            # None}` is how OpenProject spells detaching one. Resolution is
            # skipped because there is no id to verify -- that is the point.
            linked[name] = {"href": None}
        elif name in LINKED_FIELDS:
            href = resolved.get(name)
            if not href:
                raise WriteRejectedError(
                    f"cannot write {name!r}: {value!r} did not resolve to anything this "
                    "instance has"
                )
            linked[name] = {"href": href}
        elif name in FORMATTABLE_FIELDS:
            body[name] = {"raw": value}
        elif name in PLAIN_FIELDS:
            body[PLAIN_FIELDS[name]] = value
        else:
            raise WriteRejectedError(
                f"cannot write {name!r}: this adapter writes {', '.join(sorted(WRITABLE_FIELDS))}"
            )
    if linked:
        body["_links"] = linked
    if lock_version is not None:
        body["lockVersion"] = lock_version
    return body


def unknown_field_names(
    body: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> tuple[str, ...]:
    """Names in ``body`` that the instance's schema does not have.

    The check the form cannot make for us. An empty schema reports nothing
    rather than everything: a form that could not be read is not evidence that
    every name is wrong, and :func:`divergences` still stands behind this.
    """
    if not schema:
        return ()
    named = [key for key in body if key not in _NOT_COMPARED and key != "_links"]
    named += list(body.get("_links", {}))
    return tuple(name for name in named if name not in schema)


def divergences(sent: Mapping[str, Any], answered: Mapping[str, Any]) -> tuple[str, ...]:
    """What was asked for and is not so, according to the provider's own answer.

    One line per field, phrased for a person. An empty result is the only thing
    that makes a 200 mean what it appears to mean.
    """
    found: list[str] = []
    for key, value in sent.items():
        if key in _NOT_COMPARED:
            continue
        if key == "_links":
            found.extend(_link_divergences(value, answered.get("_links", {})))
            continue
        if key not in answered:
            found.append(
                f"{key}: asked for {value!r}, and the provider's answer does not mention it"
            )
            continue
        got = answered[key]
        if isinstance(value, Mapping) and isinstance(got, Mapping):
            # A formattable value comes back with `format` and `html` beside
            # the text. Only what was sent is what was asked for.
            differing = {k: got.get(k) for k in value if got.get(k) != value[k]}
            if differing:
                found.append(f"{key}: asked for {value!r}, got {differing!r}")
        elif got != value:
            found.append(f"{key}: asked for {value!r}, got {got!r}")
    return tuple(found)


def _link_divergences(sent: Any, answered: Any) -> list[str]:
    if not isinstance(sent, Mapping) or not isinstance(answered, Mapping):
        return []
    found: list[str] = []
    for name, link in sent.items():
        wanted = (link or {}).get("href")
        got = (answered.get(name) or {}).get("href")
        if wanted is None:
            # Asking for no link at all -- detaching a parent. Absence IS the
            # answer, and reading it as "the provider did not mention it" would
            # turn every successful detach into a reported failure.
            if got is not None:
                found.append(f"{name}: asked for it to be cleared, got {got!r}")
            continue
        if got is None:
            found.append(f"{name}: asked for {wanted!r}, and it is not in the provider's answer")
        elif got != wanted:
            found.append(f"{name}: asked for {wanted!r}, got {got!r}")
    return found
