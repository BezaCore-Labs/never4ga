"""In-memory GraphIndex.

Adjacency lists plus a breadth-first bounded walk -- structurally the same shape
as the SQLite recursive-CTE traversal of core/06 section 14, which is why this
fake can stand in for ``SQLiteGraphIndex``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Sequence

from never4ga.domain.capabilities import GraphIndexCapability
from never4ga.domain.identity import ConceptId
from never4ga.errors import CapabilityNotSupportedError
from never4ga.ports.graph_index import Direction, GraphEdge, GraphNeighbor, GraphPathStep

__all__ = ["InMemoryGraphIndex"]


class InMemoryGraphIndex:
    def __init__(self) -> None:
        self._edges: list[GraphEdge] = []

    @property
    def capabilities(self) -> frozenset[GraphIndexCapability]:
        # Deliberately a subset: core/06 section 13 expects the first backend to
        # implement only part of the contract and to say so.
        return frozenset({GraphIndexCapability.NEIGHBORS, GraphIndexCapability.RECURSIVE_TRAVERSAL})

    def add_edge(self, edge: GraphEdge) -> None:
        if edge not in self._edges:
            self._edges.append(edge)

    def replace_outgoing_edges(self, source: ConceptId, edges: Sequence[GraphEdge]) -> None:
        self._edges = [edge for edge in self._edges if edge.source != source]
        for edge in edges:
            self.add_edge(edge)

    def remove_document(self, concept_id: ConceptId) -> None:
        self._edges = [
            edge for edge in self._edges if edge.source != concept_id and edge.target != concept_id
        ]

    def _incident(
        self,
        concept_id: ConceptId,
        direction: Direction,
        relation_types: tuple[str, ...],
    ) -> Iterator[GraphNeighbor]:
        for edge in self._edges:
            if relation_types and edge.relation_type not in relation_types:
                continue
            if edge.source == concept_id and direction in (Direction.OUTGOING, Direction.BOTH):
                yield GraphNeighbor(edge.target, edge.relation_type, Direction.OUTGOING)
            if edge.target == concept_id and direction in (Direction.INCOMING, Direction.BOTH):
                yield GraphNeighbor(edge.source, edge.relation_type, Direction.INCOMING)

    def neighbors(
        self,
        concept_id: ConceptId,
        *,
        direction: Direction = Direction.BOTH,
        relation_types: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> Sequence[GraphNeighbor]:
        found = list(self._incident(concept_id, direction, relation_types))
        return tuple(found[:limit] if limit is not None else found)

    def traverse(
        self,
        start: ConceptId,
        *,
        max_depth: int,
        direction: Direction = Direction.BOTH,
        relation_types: tuple[str, ...] = (),
    ) -> Sequence[GraphPathStep]:
        if max_depth < 1:
            raise ValueError(f"max_depth must be at least 1, got {max_depth}")
        seen = {start}
        steps: list[GraphPathStep] = []
        frontier: deque[tuple[ConceptId, int]] = deque([(start, 0)])
        while frontier:
            node, depth = frontier.popleft()
            if depth >= max_depth:
                continue
            for neighbor in self._incident(node, direction, relation_types):
                if neighbor.concept_id in seen:
                    continue
                seen.add(neighbor.concept_id)
                steps.append(
                    GraphPathStep(
                        concept_id=neighbor.concept_id,
                        depth=depth + 1,
                        relation_type=neighbor.relation_type,
                        via=node,
                    )
                )
                frontier.append((neighbor.concept_id, depth + 1))
        return tuple(steps)

    def shortest_path(
        self,
        start: ConceptId,
        end: ConceptId,
        *,
        relation_types: tuple[str, ...] = (),
    ) -> Sequence[GraphPathStep]:
        raise CapabilityNotSupportedError("InMemoryGraphIndex does not support 'shortest_path'")

    def clear(self) -> None:
        self._edges.clear()
