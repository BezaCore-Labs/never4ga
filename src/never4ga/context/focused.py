"""Focused context: three retrievers, fused (core/07).

Startup answers "where am I". Focused answers "what about *this*" -- and it is
the first place all three retrieval lanes run together.

The order is core/07's, not an implementation convenience:

    scope (Stage A)
      -> structural metadata reduces the candidate universe (Stage B)
      -> lexical retrieval runs *inside* that reduction (Stage D)
      -> relationships expand from what it found (Stage E)
      -> rank fusion (core/06 section 7)
      -> budget

Reducing first is what keeps the vault from being loaded to answer a question
about one workspace, and it is why a perfect lexical match in somebody else's
workspace never appears.

Fusion is deliberately the only place the lanes meet. Each produces values the
others cannot be compared against, so they meet as ranks or not at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from never4ga.context.authority import apply_lifecycle_rules
from never4ga.context.budget import apply_budget
from never4ga.context.foreign import foreign_item
from never4ga.context.freshness import Clock, stale_since, utc_now
from never4ga.context.fusion import DEFAULT_K, FusedCandidate, reciprocal_rank_fusion
from never4ga.context.scope import ScopeResolver
from never4ga.context.signals import collect_signals
from never4ga.domain.context import (
    ContextDepth,
    ContextItem,
    ContextPack,
    ContextRequest,
    PackCategory,
)
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.relations import corroborates
from never4ga.domain.retrieval import RetrievalCandidate
from never4ga.domain.scope import ResolvedScope
from never4ga.layout import VaultRoot
from never4ga.ports.context_signal_provider import ContextSignalProvider
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.graph_index import Direction, GraphIndex
from never4ga.ports.metadata_index import MetadataIndex, MetadataQuery, MetadataRecord
from never4ga.ports.text_index import TextIndex, TextQuery

__all__ = ["KNOWLEDGE_TYPES", "MAX_EXPANDED", "RELATION_DEPTH", "FocusedContextAssembler"]

#: The types that live in `30_Knowledge/` and belong to no workspace.
KNOWLEDGE_TYPES: Final = ("knowledge", "map")

#: How far relationship traversal reaches from a lexical hit. Depth 1 is what
#: core/07 section 12's `relation_depends_on_depth_1` reason names; deep context
#: takes a second hop (core/04 section 18).
RELATION_DEPTH: Final = 1

#: How many lexical hits are expanded through the graph. Expanding every hit
#: turns one query into an unbounded number of graph reads, and startup and
#: focused context are both required to stay bounded.
MAX_EXPANDED: Final = 10


#: Retrieved documents keep the category their type implies, so a per-category
#: cap means the same thing in a focused pack as in a startup one. Anything not
#: listed is RETRIEVED rather than mislabelled.
_CATEGORY_OF_TYPE: Final = {
    "workspace": PackCategory.WORKSPACE,
    "context": PackCategory.CONTEXT,
    "standard": PackCategory.STANDARD,
    "decision": PackCategory.DECISION,
    "task": PackCategory.TASK,
    "activity_log": PackCategory.ACTIVITY,
}


def _category_of(record: MetadataRecord) -> PackCategory:
    return _CATEGORY_OF_TYPE.get(record.concept_type, PackCategory.RETRIEVED)


def _is_archived(path: VaultPath) -> bool:
    """Whether a document lives under `90_Archive/` (core/07 Stage B).

    The exclusion is by **location**, and it has to be: knowledge is admitted by
    type, and archiving a note moves the file without restamping it, so an
    archived note is still `type: knowledge`. Location is the only thing that
    tells the two apart.

    Applied to the Stage B result rather than to each lane, because every lane
    downstream works from it -- lexical searches it, the vector lane scopes to
    its ids, and relational traversal refuses a neighbour that is not in it. One
    guard is therefore the whole rule, and a lane added later inherits it
    instead of having to remember it.
    """
    return bool(path.segments) and path.segments[0] == str(VaultRoot.ARCHIVE)


class FocusedContextAssembler:
    """Assembles a focused Context Pack from a query, mechanically."""

    def __init__(
        self,
        resolver: ScopeResolver,
        metadata_index: MetadataIndex,
        text_index: TextIndex,
        graph_index: GraphIndex,
        document_store: DocumentStore | None = None,
        signal_providers: Sequence[ContextSignalProvider] = (),
        k: int = DEFAULT_K,
        relation_depth: int = RELATION_DEPTH,
        include_knowledge: bool = False,
        now: Clock = utc_now,
    ) -> None:
        self._now = now
        self._resolver = resolver
        self._metadata_index = metadata_index
        self._text_index = text_index
        self._graph_index = graph_index
        self._document_store = document_store
        self._signal_providers = tuple(signal_providers)
        self._k = k
        self._relation_depth = relation_depth
        self._include_knowledge = include_knowledge

    def assemble(self, request: ContextRequest) -> ContextPack:
        if request.depth is not ContextDepth.FOCUSED:
            raise NotImplementedError(
                f"{request.depth.value!r} context is not this assembler's; it "
                "builds focused packs only"
            )
        scope = self._resolver.resolve(request.scope)
        signals, degraded = collect_signals(self._signal_providers, request, scope)
        fused = self.retrieve(request, scope)

        items, usage = apply_budget(self._items(fused), request.budget)
        return ContextPack(
            scope=scope,
            items=items,
            signals=signals,
            usage=usage,
            degraded_providers=degraded,
            client=request.client,
            task_given=request.task_given,
        )

    def retrieve(self, request: ContextRequest, scope: ResolvedScope) -> list[FusedCandidate]:
        """Stages B, D and E, fused. Deep context runs the same lanes, wider."""
        in_scope = self._reduce(scope)
        candidates = list(self._lexical(request, in_scope))
        candidates.extend(self._relational(candidates, in_scope))
        fused = reciprocal_rank_fusion(candidates, k=self._k)
        # core/06 section 7's last line: authority / freshness / lifecycle rules
        # run *after* fusion, because "still in force" is not a kind of
        # relevance and no retriever can answer it.
        return list(apply_lifecycle_rules(fused, self._graph_index))

    # -- the stages -------------------------------------------------------

    def _reduce(self, scope: ResolvedScope) -> dict[ConceptId, MetadataRecord]:
        """Stage B. Everything this workspace and its parents can offer.

        Reducing by structure before searching is the difference between a
        bounded question and a vault-wide one.
        """
        records: dict[ConceptId, MetadataRecord] = {}
        for workspace_id in (scope.workspace_id, *scope.parent_chain):
            for record in self._metadata_index.query(MetadataQuery(workspace_ids=(workspace_id,))):
                records[record.concept_id] = record
        if self._include_knowledge:
            # `30_Knowledge/` belongs to no workspace, so a workspace filter
            # excludes it however relevant it is. Deep admits it: durable
            # knowledge is exactly what a broad investigation is looking for
            # (core/04 section 18, "broader Knowledge").
            for record in self._metadata_index.query(MetadataQuery(types=KNOWLEDGE_TYPES)):
                records.setdefault(record.concept_id, record)
        return {
            concept_id: record
            for concept_id, record in records.items()
            if not _is_archived(record.path)
        }

    def _lexical(
        self, request: ContextRequest, in_scope: dict[ConceptId, MetadataRecord]
    ) -> list[RetrievalCandidate]:
        """Stage D, inside the reduction rather than across the vault."""
        query = TextQuery(
            terms=request.terms,
            phrases=request.phrases,
            exact_identifiers=request.exact_identifiers,
            concept_ids=tuple(in_scope),
            # A pile of foreign notes belongs to no workspace (core/02 section
            # 3.3), so no reduction to one can name it. It is admitted beside
            # the scope instead, and never another workspace's concepts.
            include_paths=True,
        )
        if query.is_empty:
            return []
        if in_scope:
            return list(self._text_index.search(query))
        # Nothing of the workspace's own to search, as with a new workspace
        # beside a pile of foreign notes. An empty scope reads as
        # "everything" to the index, so the pile is taken from an unscoped
        # search and renumbered as the lane that found it would have.
        pile = [
            candidate
            for candidate in self._text_index.search(replace(query, concept_ids=()))
            if candidate.concept_id is None
        ]
        return [replace(candidate, rank=rank) for rank, candidate in enumerate(pile, start=1)]

    def _relational(
        self,
        found: Sequence[RetrievalCandidate],
        in_scope: dict[ConceptId, MetadataRecord],
    ) -> list[RetrievalCandidate]:
        """Stage E. What the hits point at, and what points at them.

        The lane **corroborates as well as expands**, but only through a
        relation somebody asserted. If traversal skipped every lexical hit, the
        property `context/fusion.py` exists for (a document two retrievers found
        independently outranks one a single retriever put first) could never
        occur between these two lanes.

        Corroborating through *every* edge is worse than not corroborating at
        all. `links_to` is derived from a Markdown link, and documents that cite
        each other densely would vote each other up and push the real answers
        down. So a derived edge introduces a document and never seconds one,
        which is `never4ga.domain.relations`' whole distinction. `superseded_by`
        is excluded for a different reason: it is the one registered relation
        whose meaning is negative, and corroborating on it would raise a
        document for being out of date.

        A document reached only by traversal *can* still lead a result list.
        That is intended, and an evaluation corpus is what should say whether
        it should stand.

        ``visited`` is cycle protection and nothing else: two documents pointing
        at each other must not traverse forever, and no document votes twice.
        """
        already_found = {candidate.concept_id for candidate in found}
        visited: set[ConceptId] = set()
        expanded: list[RetrievalCandidate] = []
        rank = 0
        frontier = list(found)[:MAX_EXPANDED]
        for hop in range(1, self._relation_depth + 1):
            next_frontier: list[RetrievalCandidate] = []
            for candidate in frontier:
                if candidate.concept_id is None:
                    # A foreign note's chunk is in no graph: it has
                    # no identity a relation could name, so traversal has
                    # nothing to expand. It reaches the pack by the lexical
                    # lane alone.
                    continue
                for neighbor in self._graph_index.neighbors(
                    candidate.concept_id, direction=Direction.BOTH
                ):
                    if neighbor.concept_id in visited or neighbor.concept_id not in in_scope:
                        continue
                    if neighbor.concept_id in already_found and not corroborates(
                        neighbor.relation_type
                    ):
                        # Derived edges expand, never second. Not marked
                        # visited: an asserted relation reaching the same
                        # document later is still entitled to corroborate it.
                        continue
                    visited.add(neighbor.concept_id)
                    rank += 1
                    reached = RetrievalCandidate(
                        concept_id=neighbor.concept_id,
                        retriever="graph",
                        rank=rank,
                        reason=AcquisitionReason.of(
                            ReasonCode.RELATION_DEPTH,
                            detail=f"{neighbor.relation_type} depth {hop}",
                        ),
                    )
                    expanded.append(reached)
                    next_frontier.append(reached)
            # Each hop expands from what the previous one reached, and the same
            # cap applies: a second hop off ten neighbours is still bounded.
            frontier = next_frontier[:MAX_EXPANDED]
        return expanded

    # -- assembly ---------------------------------------------------------

    def _items(self, fused: Sequence[FusedCandidate]) -> list[ContextItem]:
        items: list[ContextItem] = []
        for priority, candidate in enumerate(fused):
            if candidate.concept_id is None:
                item = foreign_item(candidate, self._document_store, priority=priority)
                if item is not None:
                    items.append(item)
                continue
            record = self._metadata_index.get(candidate.concept_id)
            if record is None:
                continue
            document = self._document_for(record)
            items.append(
                ContextItem(
                    concept_id=record.concept_id,
                    path=record.path,
                    title=record.title,
                    priority=priority,
                    reason=candidate.reasons[0],
                    body=document.body if document is not None else None,
                    category=_category_of(record),
                    stale_since=stale_since(document, self._now()),
                )
            )
        return items

    def _document_for(self, record: MetadataRecord) -> StoredDocument | None:
        if self._document_store is None:
            return None
        return self._document_store.get(record.concept_id)
