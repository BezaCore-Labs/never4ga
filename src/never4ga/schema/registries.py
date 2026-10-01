"""Registry documents -- controlled vocabulary the vault carries itself.

core/02 section 22: "Initial bootstrapping may hard-code the core values defined
in this specification. Before the runtime is considered mature, the canonical
registries SHOULD be represented inside `50_System/Schemas/` so the system can
describe its own vocabulary." The Domain Registry is the first one to make that
move, because it is the one the user actually curates (section 9.1).

The machine-readable shape -- ``registry: domains`` naming which vocabulary, and
``values`` holding it -- is a registered-field choice this module makes, since
section 21.17 says the fields are "registered schema fields defined by the
registry profile" and defines no such profile. The two fields are registered on
the `registry` type.

A registry is authoritative when present, whole or not at all: a malformed
``values`` yields nothing rather than a partial vocabulary, because validating
against half a registry would report half the truth. The damage itself is a
validation error (`invalid_registry_values`), and consumers fall back to the
bootstrap :data:`~never4ga.schema.vocabulary.DOMAIN_VALUES`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final, Protocol

from never4ga.layout import DOMAIN_REGISTRY

__all__ = ["DOMAIN_REGISTRY_KIND", "domains_from_registry", "registered_domains"]

#: The value of the ``registry`` field that marks the Domain Registry.
DOMAIN_REGISTRY_KIND: Final = "domains"


def domains_from_registry(frontmatter: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    """The domain vocabulary a registry document declares, or None.

    None means "this is not a usable Domain Registry" -- wrong type, another
    vocabulary's registry, or malformed values -- and the caller falls back to
    the bootstrap list. It never means an empty vocabulary: an empty ``values``
    list is a registry that registers nothing, and is returned as ``()``.
    """
    if frontmatter is None:
        return None
    if frontmatter.get("type") != "registry":
        return None
    if frontmatter.get("registry") != DOMAIN_REGISTRY_KIND:
        return None
    values = frontmatter.get("values")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None
    return tuple(values)


class _HasPathAndFrontmatter(Protocol):
    @property
    def path(self) -> Any: ...
    @property
    def frontmatter(self) -> Mapping[str, Any]: ...


def registered_domains(documents: Iterable[_HasPathAndFrontmatter]) -> tuple[str, ...] | None:
    """The domain vocabulary among a set of documents, or None.

    A convenience for callers that already hold the vault's concepts -- the
    doctor, a whole-vault validation -- so each finds the registry the same way:
    by its reserved path, never by scanning for the first document that looks
    like one.
    """
    for document in documents:
        if document.path == DOMAIN_REGISTRY:
            return domains_from_registry(document.frontmatter)
    return None
