"""Canonical and external identity.

Specification:

- core/02 section 5.1 -- every concept carries a stable UUIDv7 ``id`` in
  canonical lowercase hyphenated form, independent of filename and path.
- core/06 section 3 -- a backend-native row/node/point ID is never canonical
  Never4gA identity.
- core/08 section 15 -- external memory/provider IDs map to, but never replace,
  Never4gA UUIDs.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Final

from never4ga.errors import IdentityError

__all__ = [
    "CANONICAL_UUID_PATTERN",
    "UUID_VERSION",
    "ConceptId",
    "ExternalId",
    "SessionId",
    "WorkItemKey",
]

UUID_VERSION: Final = 7

#: Canonical lowercase hyphenated rendering required by core/02 section 5.1.
#: Deliberately stricter than ``uuid.UUID``, which also accepts uppercase,
#: braced, urn-prefixed and unhyphenated spellings. Canonical documents must
#: round-trip byte-for-byte, so exactly one spelling is accepted.
CANONICAL_UUID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass(frozen=True, slots=True, order=True)
class ConceptId:
    """The stable identity of a canonical Never4gA concept."""

    value: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, uuid.UUID):
            raise IdentityError(f"ConceptId requires a uuid.UUID, got {type(self.value).__name__}")
        if self.value.version != UUID_VERSION:
            raise IdentityError(
                f"ConceptId requires UUID version {UUID_VERSION}, got version {self.value.version}"
            )

    @classmethod
    def new(cls) -> ConceptId:
        """Mint a fresh time-ordered identity."""
        return cls(uuid.uuid7())

    @classmethod
    def parse(cls, raw: str) -> ConceptId:
        """Parse the canonical lowercase hyphenated rendering."""
        if not isinstance(raw, str):
            raise IdentityError(
                f"Never4gA identities are UUIDv7 strings, not {type(raw).__name__}; "
                "backend row IDs are not canonical identity"
            )
        if not CANONICAL_UUID_PATTERN.match(raw):
            raise IdentityError(f"{raw!r} is not a canonical lowercase hyphenated UUID")
        return cls(uuid.UUID(raw))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True, order=True)
class SessionId:
    """The identity of one agent session.

    core/04 section 17 puts `session_id` at the top of a startup response, and
    Never4gA mints it. A client-supplied identifier cannot be trusted to be
    unique across the three clients on one machine, nor to sort by time -- and
    sorting by time is the whole question that "the most recent session in
    this workspace" asks.

    Deliberately not a :class:`ConceptId`, and not a subclass of one. A session
    is working state (core/08 section 1), never canonical content, and keeping
    the types apart is what stops a session identity reaching frontmatter.
    """

    value: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, uuid.UUID):
            raise IdentityError(f"SessionId requires a uuid.UUID, got {type(self.value).__name__}")
        if self.value.version != UUID_VERSION:
            raise IdentityError(
                f"SessionId requires UUID version {UUID_VERSION}, got version {self.value.version}"
            )

    @classmethod
    def new(cls) -> SessionId:
        """Mint a fresh time-ordered session identity."""
        return cls(uuid.uuid7())

    @classmethod
    def parse(cls, raw: str) -> SessionId:
        """Parse the canonical lowercase hyphenated rendering."""
        if not isinstance(raw, str):
            raise IdentityError(f"a session id is a UUIDv7 string, not {type(raw).__name__}")
        if not CANONICAL_UUID_PATTERN.match(raw):
            raise IdentityError(f"{raw!r} is not a canonical lowercase hyphenated UUID")
        return cls(uuid.UUID(raw))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True, order=True)
class ExternalId:
    """An identity owned by an external system, namespaced by its provider.

    Used for work-management items, external memory records, temporal-graph
    nodes and backend point/row keys. It is intentionally *not* a
    :class:`ConceptId` subclass so that no code path can accidentally treat a
    provider ID as canonical identity.
    """

    provider: str
    value: str

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise IdentityError("ExternalId requires a non-empty provider namespace")
        if not self.value.strip():
            raise IdentityError("ExternalId requires a non-empty provider-side value")

    def __str__(self) -> str:
        return f"{self.provider}:{self.value}"


@dataclass(frozen=True, slots=True, order=True)
class WorkItemKey:
    """core/03 section 17's three-term identity for an external work item.

    ``connection + project_ref + external_id``. The provider ID alone does not
    identify anything: two trackers both number a ticket ``1234``, and the same
    tracker reached through two connections is two different systems as far as
    credentials and permissions are concerned.

    Section 17 also says Never4gA "may derive an internal cache key, but the
    provider ID remains authoritative". This is that derived key, and it is
    deliberately not a :class:`ConceptId`: an external ticket does not get a
    Never4gA UUID, which is what keeps a tracker's thousands of items from
    becoming thousands of canonical Markdown concepts.

    ``project_ref`` may be empty. A connection can be defined before a project
    is chosen, and not every tracker scopes work by project; an empty reference
    means "not project-scoped" rather than "unknown".
    """

    connection: str
    project_ref: str
    external_id: ExternalId

    def __post_init__(self) -> None:
        if not self.connection.strip():
            raise IdentityError("a work item key requires the connection it came through")

    def __str__(self) -> str:
        return f"{self.connection}/{self.project_ref}/{self.external_id.value}"
