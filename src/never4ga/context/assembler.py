"""Startup context assembly (core/07 section 15).

The target shape:

    resolve cwd
      -> read workspace projection
      -> read required standards metadata
      -> read recent activity projection
      -> read PM summary cache/API
      -> return structured bounded pack

The invariants: no model call, a bounded pack, an acquisition reason on every
item, and degradation rather than failure when an optional provider breaks.

What goes in the pack, in what order and why, lives in
:mod:`never4ga.context.structural`, because deep context reads the same things
from further away. This module is the startup depth: that pass at its narrowest
reach, bounded, with its signals collected.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.context.budget import apply_budget
from never4ga.context.focused import FocusedContextAssembler
from never4ga.context.freshness import Clock, utc_now
from never4ga.context.scope import ScopeResolver
from never4ga.context.signals import collect_signals
from never4ga.context.structural import STARTUP_REACH, StructuralContext
from never4ga.domain.context import (
    ContextDepth,
    ContextItem,
    ContextPack,
    ContextRequest,
)
from never4ga.domain.scope import ResolvedScope
from never4ga.ports.context_signal_provider import ContextSignalProvider
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.graph_index import GraphIndex
from never4ga.ports.metadata_index import MetadataIndex
from never4ga.ports.text_index import TextIndex

__all__ = ["StartupContextAssembler"]


class StartupContextAssembler:
    """Assembles a bounded startup Context Pack, mechanically.

    ``document_store`` is optional: without it the pack carries titles and paths
    only, which is still a useful startup pack and is what a not-yet-indexed
    vault can offer.

    ``text_index`` and ``graph_index`` are optional for a different reason. They
    are needed only to make startup *task-aware* -- core/04 section 16's
    ``--task``, which adds a short tail of what the named task retrieved to the
    orientation the pack already carries. Without a task nothing about the pack
    changes, and without the indexes the flag degrades to what it always meant
    on its own: which client asked, and that a task was named.
    """

    def __init__(
        self,
        resolver: ScopeResolver,
        metadata_index: MetadataIndex,
        document_store: DocumentStore | None = None,
        signal_providers: Sequence[ContextSignalProvider] = (),
        text_index: TextIndex | None = None,
        graph_index: GraphIndex | None = None,
        now: Clock = utc_now,
    ) -> None:
        self._resolver = resolver
        self._structural = StructuralContext(
            metadata_index, document_store, reach=STARTUP_REACH, now=now, mark_required=True
        )
        self._signal_providers = tuple(signal_providers)
        self._retrieval = (
            FocusedContextAssembler(
                resolver=resolver,
                metadata_index=metadata_index,
                text_index=text_index,
                graph_index=graph_index,
                document_store=document_store,
                now=now,
            )
            if text_index is not None and graph_index is not None
            else None
        )

    def assemble(self, request: ContextRequest) -> ContextPack:
        if request.depth is not ContextDepth.STARTUP:
            raise NotImplementedError(
                f"{request.depth.value!r} context is not this assembler's; it "
                "builds startup packs only"
            )
        scope = self._resolver.resolve(request.scope)
        signals, degraded = collect_signals(self._signal_providers, request, scope)
        items, usage = apply_budget(self._items(request, scope), request.budget)
        return ContextPack(
            scope=scope,
            items=items,
            signals=signals,
            usage=usage,
            degraded_providers=degraded,
            client=request.client,
            task_given=request.task_given,
        )

    def _items(self, request: ContextRequest, scope: ResolvedScope) -> list[ContextItem]:
        """Orientation, and then -- only if a task was named -- what it found.

        Structure first at every depth. A task makes startup task-*aware*, not
        task-led: what the workspace is still comes before what a search
        returned, and the RETRIEVED cap keeps the tail short.
        """
        structural = self._structural.items(scope)
        if self._retrieval is None or not request.terms:
            return structural
        return [
            *structural,
            *self._structural.retrieved(
                self._retrieval.retrieve(request, scope),
                already={item.concept_id for item in structural if item.concept_id is not None},
            ),
        ]
