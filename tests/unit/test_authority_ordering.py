"""The lifecycle stage core/06 section 7 puts after fusion.

    lexical rank + vector rank + graph candidates + metadata filters
            -> rank fusion (e.g. RRF)
            -> authority / freshness / lifecycle rules

The relational lane corroborates, and a `superseded_by` edge is a relationship
like any other. Without this stage, a replaced decision gets an extra vote for
having been replaced and can outrank the decision that replaced it.

The rule is the narrow one `details/retrieval-context-memory.md` section 22
asks for ("does not prioritize superseded decision W"), not a general demotion
of superseded documents. A superseded decision is often still the right answer,
because part of it may still stand. What must never happen is that it outranks
its own successor.
"""

from __future__ import annotations

import pytest

from never4ga.adapters.fakes import InMemoryGraphIndex
from never4ga.context.authority import apply_lifecycle_rules
from never4ga.context.fusion import FusedCandidate
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.graph_index import GraphEdge

OLD = ConceptId.new()
NEW = ConceptId.new()
OTHER = ConceptId.new()
NEWEST = ConceptId.new()


def _candidate(concept_id: ConceptId, score: float) -> FusedCandidate:
    return FusedCandidate(concept_id=concept_id, score=score, contributions={}, reasons=())


@pytest.fixture
def graph() -> InMemoryGraphIndex:
    index = InMemoryGraphIndex()
    index.add_edge(GraphEdge(source=OLD, target=NEW, relation_type="superseded_by"))
    return index


class TestSupersession:
    def test_a_superseded_document_falls_below_its_successor(
        self, graph: InMemoryGraphIndex
    ) -> None:
        ordered = apply_lifecycle_rules([_candidate(OLD, 0.9), _candidate(NEW, 0.8)], graph)
        assert [candidate.concept_id for candidate in ordered] == [NEW, OLD]

    def test_it_falls_no_further_than_that(self, graph: InMemoryGraphIndex) -> None:
        """A demotion, not a burial. It keeps everything it outranked before."""
        ordered = apply_lifecycle_rules(
            [_candidate(OLD, 0.9), _candidate(NEW, 0.8), _candidate(OTHER, 0.7)], graph
        )
        assert [candidate.concept_id for candidate in ordered] == [NEW, OLD, OTHER]

    def test_a_superseded_document_whose_successor_is_absent_is_left_alone(
        self, graph: InMemoryGraphIndex
    ) -> None:
        """The rule is relational.

        A superseded decision can still be the only answer to a question.
        Demoting it whenever it appears would trade one wrong answer for
        another.
        """
        ordered = apply_lifecycle_rules([_candidate(OLD, 0.9), _candidate(OTHER, 0.8)], graph)
        assert [candidate.concept_id for candidate in ordered] == [OLD, OTHER]

    def test_an_already_correct_order_is_untouched(self, graph: InMemoryGraphIndex) -> None:
        ordered = apply_lifecycle_rules([_candidate(NEW, 0.9), _candidate(OLD, 0.8)], graph)
        assert [candidate.concept_id for candidate in ordered] == [NEW, OLD]

    def test_a_chain_resolves_all_the_way_down(self, graph: InMemoryGraphIndex) -> None:
        graph.add_edge(GraphEdge(source=NEW, target=NEWEST, relation_type="superseded_by"))
        ordered = apply_lifecycle_rules(
            [_candidate(OLD, 0.9), _candidate(NEW, 0.8), _candidate(NEWEST, 0.7)], graph
        )
        assert [candidate.concept_id for candidate in ordered] == [NEWEST, NEW, OLD]

    def test_a_supersession_cycle_terminates_rather_than_spinning(
        self, graph: InMemoryGraphIndex
    ) -> None:
        """A vault can say anything. An indexer must still finish."""
        graph.add_edge(GraphEdge(source=NEW, target=OLD, relation_type="superseded_by"))
        ordered = apply_lifecycle_rules([_candidate(OLD, 0.9), _candidate(NEW, 0.8)], graph)
        assert {candidate.concept_id for candidate in ordered} == {OLD, NEW}

    def test_other_relations_do_not_reorder_anything(self) -> None:
        index = InMemoryGraphIndex()
        index.add_edge(GraphEdge(source=OLD, target=NEW, relation_type="depends_on"))
        ordered = apply_lifecycle_rules([_candidate(OLD, 0.9), _candidate(NEW, 0.8)], index)
        assert [candidate.concept_id for candidate in ordered] == [OLD, NEW]

    def test_an_empty_list_is_not_a_special_case(self, graph: InMemoryGraphIndex) -> None:
        assert apply_lifecycle_rules([], graph) == ()


class TestTheDemotionIsALift:
    """The successor is lifted above the superseded document, not the reverse.

    Sinking the superseded document to sit under its successor is not safe: it
    pins a document that may still be the only answer to a question underneath
    one that may be irrelevant to it and ranked far down the list.
    """

    def test_a_distant_successor_is_lifted_rather_than_the_original_sunk(
        self, graph: InMemoryGraphIndex
    ) -> None:
        far = [_candidate(OLD, 0.9)]
        far += [_candidate(ConceptId.new(), 0.9 - n / 100) for n in range(1, 20)]
        far.append(_candidate(NEW, 0.1))
        ordered = apply_lifecycle_rules(far, graph)
        positions = [candidate.concept_id for candidate in ordered]
        assert positions.index(NEW) == 0
        assert positions.index(OLD) == 1

    def test_everything_between_them_keeps_its_order(self, graph: InMemoryGraphIndex) -> None:
        middle = [ConceptId.new() for _ in range(3)]
        ordered = apply_lifecycle_rules(
            [
                _candidate(OLD, 0.9),
                *(_candidate(one, 0.5) for one in middle),
                _candidate(NEW, 0.1),
            ],
            graph,
        )
        assert [candidate.concept_id for candidate in ordered] == [NEW, OLD, *middle]


class TestForeignNotes:
    def test_a_foreign_note_keeps_the_place_it_earned(self, graph: InMemoryGraphIndex) -> None:
        # A note in foreign material (core/02 section 3.3) has no identity and
        # is in no graph, so no supersession can move it, and reordering
        # around it must not trip on an owner that has no identity.
        note = FusedCandidate(
            concept_id=None,
            source_path=VaultPath.parse("Projects/note.md"),
            score=0.95,
            contributions={},
            reasons=(),
        )
        ordered = apply_lifecycle_rules([note, _candidate(OLD, 0.9), _candidate(NEW, 0.8)], graph)
        assert [candidate.owner for candidate in ordered] == [
            "path:Projects/note.md",
            str(NEW),
            str(OLD),
        ]
