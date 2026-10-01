"""MetadataIndex -- the derived projection of canonical frontmatter.

core/07 Stage B uses structural metadata to include and exclude candidates
before any search runs. This is a *projection* (core/06 section 2): it must be
disposable and rebuildable from Markdown, which is why ``clear`` is part of the
contract.

Queries are structured filters, never SQL. core/06 section 25 Test G forbids
requiring callers to write backend query syntax.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId

__all__ = ["MetadataIndex", "MetadataQuery", "MetadataRecord"]


@dataclass(frozen=True, slots=True)
class MetadataRecord:
    """Projected frontmatter for one concept.

    ``extra`` carries everything Never4gA does not model natively, so unknown
    extension fields survive projection (core/00 #15).
    """

    concept_id: ConceptId
    concept_type: str
    path: VaultPath
    title: str
    workspace_id: ConceptId | None = None
    tags: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    lifecycle: str | None = None
    status: str | None = None
    authority: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))


@dataclass(frozen=True, slots=True)
class MetadataQuery:
    """A structured metadata filter.

    Every populated field narrows the result set (AND); values within one field
    are alternatives (any-of). An empty query matches everything.
    """

    types: tuple[str, ...] = ()
    workspace_ids: tuple[ConceptId, ...] = ()
    tags: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    lifecycles: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    authorities: tuple[str, ...] = ()
    limit: int | None = None


@runtime_checkable
class MetadataIndex(Protocol):
    def upsert(self, record: MetadataRecord) -> None: ...

    def remove(self, concept_id: ConceptId) -> None: ...

    def get(self, concept_id: ConceptId) -> MetadataRecord | None: ...

    def query(self, query: MetadataQuery) -> Sequence[MetadataRecord]: ...

    def clear(self) -> None:
        """Discard the whole projection so it can be rebuilt from canonical sources."""
        ...
