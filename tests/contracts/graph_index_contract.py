"""GraphIndex contract (core/06 sections 12-15, core/07 Stage E)."""

from __future__ import annotations

import pytest

from never4ga.domain.capabilities import GraphIndexCapability
from never4ga.domain.identity import ConceptId
from never4ga.errors import CapabilityNotSupportedError
from never4ga.ports.graph_index import Direction, GraphEdge, GraphIndex


class GraphIndexContract:
    @pytest.fixture
    def index(self) -> GraphIndex:
        raise NotImplementedError("supply a GraphIndex fixture")

    def test_declares_capabilities(self, index: GraphIndex) -> None:
        assert GraphIndexCapability.NEIGHBORS in index.capabilities

    def test_outgoing_neighbors(self, index: GraphIndex, concept_ids: list[ConceptId]) -> None:
        a, b, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        neighbors = index.neighbors(a, direction=Direction.OUTGOING)
        assert [n.concept_id for n in neighbors] == [b]
        assert neighbors[0].relation_type == "depends_on"

    def test_incoming_neighbors(self, index: GraphIndex, concept_ids: list[ConceptId]) -> None:
        a, b, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        assert [n.concept_id for n in index.neighbors(b, direction=Direction.INCOMING)] == [a]
        assert index.neighbors(b, direction=Direction.OUTGOING) == ()

    def test_both_directions(self, index: GraphIndex, concept_ids: list[ConceptId]) -> None:
        a, b, c, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        index.add_edge(GraphEdge(source=c, target=a, relation_type="supersedes"))
        assert {n.concept_id for n in index.neighbors(a, direction=Direction.BOTH)} == {b, c}

    def test_relation_type_filter(self, index: GraphIndex, concept_ids: list[ConceptId]) -> None:
        a, b, c, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        index.add_edge(GraphEdge(source=a, target=c, relation_type="supersedes"))
        found = index.neighbors(a, direction=Direction.OUTGOING, relation_types=("supersedes",))
        assert [n.concept_id for n in found] == [c]

    def test_neighbors_of_unknown_node_is_empty(self, index: GraphIndex) -> None:
        assert index.neighbors(ConceptId.new(), direction=Direction.BOTH) == ()

    def test_duplicate_edges_are_not_stored_twice(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        a, b, *_ = concept_ids
        edge = GraphEdge(source=a, target=b, relation_type="depends_on")
        index.add_edge(edge)
        index.add_edge(edge)
        assert len(index.neighbors(a, direction=Direction.OUTGOING)) == 1

    def test_traverse_is_bounded_by_depth(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        # core/06 section 13 and core/07 Stage E: traversal is bounded.
        a, b, c, d, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="child_of"))
        index.add_edge(GraphEdge(source=b, target=c, relation_type="child_of"))
        index.add_edge(GraphEdge(source=c, target=d, relation_type="child_of"))
        reached = index.traverse(a, max_depth=2, direction=Direction.OUTGOING)
        assert {step.concept_id for step in reached} == {b, c}
        assert {step.depth for step in reached} == {1, 2}

    def test_traverse_reports_the_edge_it_arrived_by(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        a, b, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        (step,) = index.traverse(a, max_depth=1, direction=Direction.OUTGOING)
        assert step.relation_type == "depends_on"
        assert step.via == a

    def test_traverse_terminates_on_cycles(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        a, b, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="related"))
        index.add_edge(GraphEdge(source=b, target=a, relation_type="related"))
        reached = index.traverse(a, max_depth=10, direction=Direction.OUTGOING)
        assert [step.concept_id for step in reached] == [b]

    def test_traverse_rejects_unbounded_depth(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        with pytest.raises(ValueError, match="max_depth"):
            index.traverse(concept_ids[0], max_depth=0, direction=Direction.OUTGOING)

    def test_replace_outgoing_edges_swaps_what_the_document_declares(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        a, b, c, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        index.replace_outgoing_edges(a, [GraphEdge(source=a, target=c, relation_type="supports")])
        neighbors = index.neighbors(a, direction=Direction.OUTGOING)
        assert [(n.concept_id, n.relation_type) for n in neighbors] == [(c, "supports")]

    def test_replace_outgoing_edges_leaves_other_documents_alone(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        # Reindexing one changed document must not delete relations pointing at
        # it, which their own sources would then have to be reindexed to restore.
        a, b, c, *_ = concept_ids
        index.add_edge(GraphEdge(source=c, target=a, relation_type="supersedes"))
        index.replace_outgoing_edges(a, [GraphEdge(source=a, target=b, relation_type="depends_on")])
        assert [n.concept_id for n in index.neighbors(a, direction=Direction.INCOMING)] == [c]

    def test_replace_outgoing_edges_with_nothing_clears_them(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        a, b, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        index.replace_outgoing_edges(a, [])
        assert index.neighbors(a, direction=Direction.OUTGOING) == ()

    def test_remove_document_drops_edges_on_both_sides(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        a, b, c, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        index.add_edge(GraphEdge(source=c, target=b, relation_type="depends_on"))
        index.remove_document(b)
        assert index.neighbors(a, direction=Direction.OUTGOING) == ()
        assert index.neighbors(c, direction=Direction.OUTGOING) == ()

    def test_undeclared_capability_fails_loudly(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        # core/06 section 13: the first backend may implement only a subset,
        # and capabilities must be discoverable rather than guessed.
        if GraphIndexCapability.SHORTEST_PATH in index.capabilities:
            pytest.skip("implementation declares shortest_path")
        with pytest.raises(CapabilityNotSupportedError):
            index.shortest_path(concept_ids[0], concept_ids[1])

    def test_clear_makes_the_projection_disposable(
        self, index: GraphIndex, concept_ids: list[ConceptId]
    ) -> None:
        a, b, *_ = concept_ids
        index.add_edge(GraphEdge(source=a, target=b, relation_type="depends_on"))
        index.clear()
        assert index.neighbors(a, direction=Direction.BOTH) == ()
