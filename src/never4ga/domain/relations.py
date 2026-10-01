"""Relation vocabulary, and the line between asserted and derived.

core/02 section 17.2 registers the canonical relation types. A document carries
them in `relations:` because somebody decided the relationship holds: `A
depends_on B` is a claim, and `A superseded_by B` is a ruling.

Never4gA also *derives* edges that nobody typed -- a Markdown link in a body, a
workspace's parent. core/06 section 12 makes both canonical graph sources and
they belong in the index, but they are not assertions. A specification that
cites six neighbouring specifications has said nothing about which of them
answers a question; it has said that specifications cross-reference each other.

That difference matters in retrieval. If a derived edge could corroborate a
lexical hit, a set of documents that cite each other densely would fill the top
results for any query touching one of them and push the real answers down. A
typed relation does not behave that way: somebody had to mean it.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CLOSES",
    "DERIVED_RELATIONS",
    "LIFECYCLE_RELATIONS",
    "LINK_RELATION",
    "NON_CORROBORATING",
    "PARENT_RELATION",
    "SUPERSEDED_BY",
    "corroborates",
]

#: A Markdown link in a document body, resolved to a concept.
LINK_RELATION: Final = "links_to"

#: A workspace's parent, from `parent` in its frontmatter.
PARENT_RELATION: Final = "parent"

#: core/02 section 21.11: a superseded decision MUST identify its replacement
#: with this relation when the replacement is known.
SUPERSEDED_BY: Final = "superseded_by"

#: core/02 section 17.2: written on the record of completion, naming the plan
#: it finished.
#: It exists so that a plan whose work is done can be *detected* saying it is
#: still a draft, rather than depending on somebody remembering to edit it.
CLOSES: Final = "closes"

#: Edges Never4gA synthesised rather than edges somebody wrote.
DERIVED_RELATIONS: Final = frozenset({LINK_RELATION, PARENT_RELATION})

#: Relations about a document's *status* rather than its *subject*.
#:
#: `depends_on`, `implements`, `supports`, `applies_to`, `related_to` and
#: `blocks` all say two documents belong together, which is a reason for one to
#: vouch for the other. `superseded_by` says one of them is out of date and
#: `closes` says one of them finished the other's work. Neither makes a document
#: a better answer to a question, and corroborating on them raises documents for
#: being *administratively* related to something relevant. A superseded note
#: would rise because its successor matched, and a plan and every report that
#: closes it would vouch for each other and rise together.
LIFECYCLE_RELATIONS: Final = frozenset({SUPERSEDED_BY, CLOSES})

#: Relations that may introduce a document to a result but never second one:
#: the derived edges, because nobody asserted them, and the lifecycle edges,
#: because what they assert is not relevance.
#:
#: Ordering is not this rule's job. `context/authority.py` keeps a superseded
#: document below its successor; this keeps it from being lifted past everything
#: else on the way there.
NON_CORROBORATING: Final = DERIVED_RELATIONS | LIFECYCLE_RELATIONS


def corroborates(relation_type: str) -> bool:
    """May this relation give a second vote to a document already found?

    Unknown types may. core/02 section 17.3 requires unregistered relation types
    to survive, and a vault that registered its own vocabulary means those as
    much as it means `depends_on`.
    """
    return relation_type not in NON_CORROBORATING
