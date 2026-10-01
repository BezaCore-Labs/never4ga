"""Deep context: the third progressive mode (core/04 section 18).

Deep is not a fourth kind of retrieval. It is the two passes that already exist,
reaching further:

    structural   the lineage's own state, not just the workspace's
    lexical      `30_Knowledge/` admitted, which a workspace filter excludes
    relational   a second graph hop
    budget       more room, and `Logs/` reached further back

**It stays inside the workspace and its parents.** Crossing to a sibling is an
explicit named argument, never a depth: a pack that silently reaches into
another workspace has stopped being bounded, and "deep" would have become the
word for "everything". core/04 section 18's own advice is that agents escalate
progressively rather than defaulting here.

Structure still outranks retrieval. What the workspace *is* occupies the same
priority bands it does at startup, and everything found by searching sits after
it -- so a tight budget on a deep pack degrades to a startup pack rather than to
an arbitrary slice of search results.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.context.budget import apply_budget
from never4ga.context.focused import FocusedContextAssembler
from never4ga.context.freshness import Clock, utc_now
from never4ga.context.fusion import DEFAULT_K
from never4ga.context.scope import ScopeResolver
from never4ga.context.signals import collect_signals
from never4ga.context.structural import DEEP_REACH, StructuralContext
from never4ga.domain.context import ContextDepth, ContextPack, ContextRequest
from never4ga.ports.context_signal_provider import ContextSignalProvider
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.graph_index import GraphIndex
from never4ga.ports.metadata_index import MetadataIndex
from never4ga.ports.text_index import TextIndex

__all__ = ["DEEP_RELATION_DEPTH", "DeepContextAssembler"]

#: The second hop core/04 section 18 asks for by name.
DEEP_RELATION_DEPTH: int = 2


class DeepContextAssembler:
    """Assembles a bounded deep Context Pack, mechanically.

    Deep still requires no model call. It is wider, not smarter: the same
    deterministic stages over a larger candidate set.
    """

    def __init__(
        self,
        resolver: ScopeResolver,
        metadata_index: MetadataIndex,
        text_index: TextIndex,
        graph_index: GraphIndex,
        document_store: DocumentStore | None = None,
        signal_providers: Sequence[ContextSignalProvider] = (),
        k: int = DEFAULT_K,
        now: Clock = utc_now,
    ) -> None:
        self._resolver = resolver
        self._metadata_index = metadata_index
        self._structural = StructuralContext(
            metadata_index, document_store, reach=DEEP_REACH, now=now
        )
        self._retrieval = FocusedContextAssembler(
            resolver=resolver,
            metadata_index=metadata_index,
            text_index=text_index,
            graph_index=graph_index,
            document_store=document_store,
            k=k,
            relation_depth=DEEP_RELATION_DEPTH,
            include_knowledge=True,
            now=now,
        )
        self._document_store = document_store
        self._signal_providers = tuple(signal_providers)

    def assemble(self, request: ContextRequest) -> ContextPack:
        if request.depth is not ContextDepth.DEEP:
            raise NotImplementedError(
                f"{request.depth.value!r} context is not this assembler's; it "
                "builds deep packs only"
            )
        scope = self._resolver.resolve(request.scope)
        signals, degraded = collect_signals(self._signal_providers, request, scope)

        structural = self._structural.items(scope)
        retrieved = self._structural.retrieved(
            self._retrieval.retrieve(request, scope),
            already={item.concept_id for item in structural if item.concept_id is not None},
        )

        items, usage = apply_budget([*structural, *retrieved], request.budget)
        return ContextPack(
            scope=scope,
            items=items,
            signals=signals,
            usage=usage,
            degraded_providers=degraded,
            client=request.client,
            task_given=request.task_given,
        )
