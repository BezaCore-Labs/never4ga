"""GraphIndex -- relationship retrieval (core/06 sections 12-15, core/07 Stage E).

Graph semantics exist from day one. Typed relations and Markdown links are
canonical in Markdown; this port is the *projection* of them. SQLite edge tables
are the first implementation; Neo4j is a later, rebuildable alternative whose
internal node IDs never enter canonical documents.

Traversal is always bounded (core/07 section 6).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from never4ga.domain.capabilities import GraphIndexCapability
from never4ga.domain.identity import ConceptId

__all__ = ["Direction", "GraphEdge", "GraphIndex", "GraphNeighbor", "GraphPathStep"]


class Direction(enum.StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A typed relation projected from canonical Markdown."""

    source: ConceptId
    target: ConceptId
    relation_type: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.relation_type.strip():
            raise ValueError("relation_type is required")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True, slots=True)
class GraphNeighbor:
    concept_id: ConceptId
    relation_type: str
    direction: Direction


@dataclass(frozen=True, slots=True)
class GraphPathStep:
    """A node reached during a bounded traversal, with the edge it arrived by."""

    concept_id: ConceptId
    depth: int
    relation_type: str
    via: ConceptId


@runtime_checkable
class GraphIndex(Protocol):
    @property
    def capabilities(self) -> frozenset[GraphIndexCapability]: ...

    def add_edge(self, edge: GraphEdge) -> None: ...

    def replace_outgoing_edges(self, source: ConceptId, edges: Sequence[GraphEdge]) -> None:
        """Replace exactly the edges this document declares.

        Reindexing one changed document must not disturb edges other documents
        own. ``remove_document`` is the wrong tool for it -- that clears both
        directions, which would delete every relation *pointing at* this
        document and leave them missing until their own sources were reindexed.

        A document owns its outgoing relations because that is where they are
        written (core/02 section 17), so replacing them wholesale is the
        projection of an edit to its frontmatter.
        """
        ...

    def remove_document(self, concept_id: ConceptId) -> None:
        """Remove every edge incident to this concept, in either direction."""
        ...

    def neighbors(
        self,
        concept_id: ConceptId,
        *,
        direction: Direction = Direction.BOTH,
        relation_types: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> Sequence[GraphNeighbor]: ...

    def traverse(
        self,
        start: ConceptId,
        *,
        max_depth: int,
        direction: Direction = Direction.BOTH,
        relation_types: tuple[str, ...] = (),
    ) -> Sequence[GraphPathStep]:
        """Breadth-first bounded expansion. ``max_depth`` must be at least 1."""
        ...

    def shortest_path(
        self,
        start: ConceptId,
        end: ConceptId,
        *,
        relation_types: tuple[str, ...] = (),
    ) -> Sequence[GraphPathStep]:
        """Optional; raises ``CapabilityNotSupportedError`` unless declared."""
        ...

    def clear(self) -> None: ...
