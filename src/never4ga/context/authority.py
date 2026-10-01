"""The stage that runs after fusion (core/06 section 7).

    lexical rank + vector rank + graph-expanded candidates + metadata filters
            -> rank fusion (e.g. RRF)
            -> authority / freshness / lifecycle rules

Rank fusion answers "what matched". This answers "and which of these is still
in force", which is a question no retriever can answer from relevance --
`details/retrieval-context-memory.md` section 5 is explicit that semantic
retrieval is not trusted to determine authority, lifecycle or supersession, and
"those come from structured metadata and relationships".

**One rule is implemented, and it is the narrow one.** A document does not
outrank the document that superseded it. This is satisfied by lifting the
successor, never by sinking the original. That is section 22's fixture line,
"does not prioritize superseded decision W". It is deliberately not a general
demotion of everything marked superseded: a superseded document can still be
the only place part of a rule is written down, and burying it whenever it
appears would trade one wrong answer for another.

**Freshness and authority rules are not implemented.** core/02 section 21.11
maps `superseded` to `authority: informational`, so a broader rule is
available. What "fresher" means, and whether `informational` should ever lose to
`authoritative` on relevance alone, are open questions. They are absent rather
than guessed, and an evaluation corpus is what should settle them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from never4ga.context.fusion import FusedCandidate
from never4ga.domain.identity import ConceptId
from never4ga.domain.relations import SUPERSEDED_BY
from never4ga.ports.graph_index import Direction, GraphIndex

__all__ = ["apply_lifecycle_rules"]

#: A vault can express a supersession cycle, and a reordering pass still has to
#: finish. Chains in practice are one or two long.
_MAX_PASSES: Final = 8


def apply_lifecycle_rules(
    candidates: Sequence[FusedCandidate],
    graph: GraphIndex,
) -> tuple[FusedCandidate, ...]:
    """Reorder so that nothing outranks the document that replaced it.

    Scores are left alone. The fused score records what retrieval found, and
    rewriting it here would make the number mean two things at once -- what
    matched, and what is in force. Only the order carries the second.
    """
    ordered = list(candidates)
    if len(ordered) < 2:
        return tuple(ordered)

    present = {candidate.owner for candidate in ordered}
    # A foreign note (core/02 section 3.3) is in no graph, so nothing
    # supersedes it and it supersedes nothing; it keeps the place it earned.
    successors = {
        candidate.owner: {str(successor) for successor in _successors(graph, candidate.concept_id)}
        & present
        for candidate in ordered
        if candidate.concept_id is not None
    }
    if not any(successors.values()):
        return tuple(ordered)

    for _ in range(_MAX_PASSES):
        position = {candidate.owner: index for index, candidate in enumerate(ordered)}
        moved = False
        for index, candidate in enumerate(ordered):
            below = [
                position[successor]
                for successor in successors.get(candidate.owner, ())
                if position[successor] > index
            ]
            if not below:
                continue
            # Promote the successor to where the superseded document sits,
            # rather than demoting the superseded document to where the
            # successor sits. Both satisfy "does not outrank its successor" and
            # only this one is safe. Demotion can bury a partly superseded
            # document far down the list whenever its successor ranks poorly
            # for the query. Supersession says the newer document is at least
            # as current, never that the older one is less relevant, so the
            # pair belongs at the better of the two positions.
            ordered.insert(index, ordered.pop(max(below)))
            moved = True
            break
        if not moved:
            return tuple(ordered)
    return tuple(ordered)


def _successors(graph: GraphIndex, concept_id: ConceptId) -> set[ConceptId]:
    """What replaced this document, if anything in view did.

    Outgoing only. core/02 section 17.2 writes `superseded_by` on the document
    that was replaced, and section 17.2 also says the index MAY expose the
    reverse as `supersedes` -- reading both directions would demote the
    successor for having replaced something.
    """
    return {
        neighbor.concept_id
        for neighbor in graph.neighbors(concept_id, direction=Direction.OUTGOING)
        if neighbor.relation_type == SUPERSEDED_BY
    }
