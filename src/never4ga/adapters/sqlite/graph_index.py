"""SQLiteGraphIndex -- relationship retrieval (core/06 sections 12-15).

Typed relations are canonical in Markdown (core/02 section 17). This is their
projection and never their home: clearing it changes no document, and rebuilding
it from frontmatter recovers every edge.

`details/data-indexing-maintenance.md` section 11: "Recursive SQL/controlled
application traversal is sufficient for personal-scale v0.1." Traversal here is
one query per depth level rather than a recursive CTE. The bound is then
structural -- the loop cannot run more times than ``max_depth`` -- instead of
resting on a CTE terminating correctly on a cyclic graph, and each step keeps
the edge it arrived by, which a CTE makes harder to carry.

``shortest_path`` is deliberately not implemented. core/06 section 13 expects a
first backend to cover a subset, and section 21 requires capability to be
discoverable rather than guessed, so asking for it raises rather than silently
returning something worse.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Final

from never4ga.domain.capabilities import GraphIndexCapability
from never4ga.domain.identity import ConceptId
from never4ga.errors import CapabilityNotSupportedError
from never4ga.ports.graph_index import (
    Direction,
    GraphEdge,
    GraphNeighbor,
    GraphPathStep,
)

__all__ = ["SQLiteGraphIndex"]

_OUTGOING: Final = "SELECT target_id AS other, relation_type FROM relations WHERE source_id = ?"
_INCOMING: Final = "SELECT source_id AS other, relation_type FROM relations WHERE target_id = ?"


class SQLiteGraphIndex:
    """A GraphIndex over the ``relations`` projection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def capabilities(self) -> frozenset[GraphIndexCapability]:
        return frozenset(
            {
                GraphIndexCapability.NEIGHBORS,
                GraphIndexCapability.RECURSIVE_TRAVERSAL,
            }
        )

    # -- writing ----------------------------------------------------------

    def add_edge(self, edge: GraphEdge) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO relations (source_id, relation_type, target_id, attributes_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (source_id, relation_type, target_id)
                    DO UPDATE SET attributes_json = excluded.attributes_json
                """,
                (
                    str(edge.source),
                    edge.relation_type,
                    str(edge.target),
                    json.dumps(dict(edge.attributes), default=str),
                ),
            )

    def replace_outgoing_edges(self, source: ConceptId, edges: Sequence[GraphEdge]) -> None:
        """Replace exactly the relations this document declares."""
        with self._connection:
            self._connection.execute("DELETE FROM relations WHERE source_id = ?", (str(source),))
        for edge in edges:
            self.add_edge(edge)

    def remove_document(self, concept_id: ConceptId) -> None:
        """Remove every edge incident to this concept, in either direction."""
        document_id = str(concept_id)
        with self._connection:
            self._connection.execute(
                "DELETE FROM relations WHERE source_id = ? OR target_id = ?",
                (document_id, document_id),
            )

    def clear(self) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM relations")

    # -- reading ----------------------------------------------------------

    def neighbors(
        self,
        concept_id: ConceptId,
        *,
        direction: Direction = Direction.BOTH,
        relation_types: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> Sequence[GraphNeighbor]:
        found = [
            GraphNeighbor(
                concept_id=ConceptId.parse(other),
                relation_type=relation_type,
                direction=edge_direction,
            )
            for other, relation_type, edge_direction in self._adjacent(
                (str(concept_id),), direction, relation_types
            )
        ]
        return tuple(found[:limit] if limit is not None else found)

    def traverse(
        self,
        start: ConceptId,
        *,
        max_depth: int,
        direction: Direction = Direction.BOTH,
        relation_types: tuple[str, ...] = (),
    ) -> Sequence[GraphPathStep]:
        """Breadth-first bounded expansion (core/07 section 6)."""
        if max_depth < 1:
            raise ValueError(f"max_depth must be at least 1, got {max_depth}")

        seen = {str(start)}
        frontier = {str(start)}
        steps: list[GraphPathStep] = []
        for depth in range(1, max_depth + 1):
            reached: dict[str, GraphPathStep] = {}
            for source in sorted(frontier):
                for other, relation_type, _ in self._adjacent((source,), direction, relation_types):
                    if other in seen or other in reached:
                        continue
                    reached[other] = GraphPathStep(
                        concept_id=ConceptId.parse(other),
                        depth=depth,
                        relation_type=relation_type,
                        via=ConceptId.parse(source),
                    )
            if not reached:
                break
            steps.extend(reached[key] for key in sorted(reached))
            seen.update(reached)
            frontier = set(reached)
        return tuple(steps)

    def shortest_path(
        self,
        start: ConceptId,
        end: ConceptId,
        *,
        relation_types: tuple[str, ...] = (),
    ) -> Sequence[GraphPathStep]:
        """Undeclared, so it fails loudly rather than approximating (core/06 section 21)."""
        raise CapabilityNotSupportedError(
            "SQLiteGraphIndex does not support 'shortest_path'; "
            "bounded traversal is the v0.1 graph capability"
        )

    # -- internals --------------------------------------------------------

    def _adjacent(
        self,
        sources: tuple[str, ...],
        direction: Direction,
        relation_types: tuple[str, ...],
    ) -> list[tuple[str, str, Direction]]:
        """Every edge leaving or arriving at ``sources``, in a stable order."""
        queries = []
        if direction in (Direction.OUTGOING, Direction.BOTH):
            queries.append((_OUTGOING, Direction.OUTGOING))
        if direction in (Direction.INCOMING, Direction.BOTH):
            queries.append((_INCOMING, Direction.INCOMING))

        found: list[tuple[str, str, Direction]] = []
        for sql, edge_direction in queries:
            filtered, parameters = sql, list(sources)
            if relation_types:
                filtered += f" AND relation_type IN ({', '.join('?' * len(relation_types))})"
                parameters.extend(relation_types)
            filtered += " ORDER BY relation_type, other"
            found.extend(
                (row["other"], row["relation_type"], edge_direction)
                for row in self._connection.execute(filtered, parameters)
            )
        return found
