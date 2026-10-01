"""Context assembly, composed once for every interface.

core/05 section 15: the CLI, the HTTP API and the MCP server call the same
application services. Context is where that matters most, because assembling it
means combining two different kinds of state -- machine-local knowledge of where
repositories are, and the vault's derived indexes -- and an interface that
combined them by hand would be the second place the rules live.

Scope resolves once, through :class:`~never4ga.services.workspaces.WorkspaceService`,
so a context request gets the same answer `workspace resolve` would give it,
including repository discovery and the marker. The assemblers below it are then
handed that answer and never repeat the work -- which is not only a saving. A
resolution carries more than a workspace id: which repository root the session
is actually in, and why that was the answer. Re-deriving it from the id alone
throws both away and substitutes the registry's, and Stage F signals read it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from never4ga.context.assembler import StartupContextAssembler
from never4ga.context.deep import DeepContextAssembler
from never4ga.context.focused import FocusedContextAssembler
from never4ga.context.scope import ScopeResolver
from never4ga.domain.context import ContextDepth, ContextPack, ContextRequest, PackCategory
from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import SessionId
from never4ga.domain.scope import ResolvedScope, ScopeRequest
from never4ga.ports.context_signal_provider import ContextSignalProvider
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.graph_index import GraphIndex
from never4ga.ports.metadata_index import MetadataIndex
from never4ga.ports.session_store import SessionStore
from never4ga.ports.text_index import TextIndex
from never4ga.services.authoring import OWNER_ACTOR, Clock, utc_now
from never4ga.services.search import parse_query
from never4ga.services.sessions import SessionService
from never4ga.services.workspaces import ScopeResolution, WorkspaceService

__all__ = ["ContextService"]

_Assembler = StartupContextAssembler | FocusedContextAssembler | DeepContextAssembler


class ContextService:
    """Startup, focused and deep Context Packs for one vault on one machine."""

    def __init__(
        self,
        workspaces: WorkspaceService,
        metadata: MetadataIndex,
        text: TextIndex,
        graph: GraphIndex,
        documents: DocumentStore | None = None,
        signal_providers: Sequence[ContextSignalProvider] = (),
        sessions: SessionStore | None = None,
        now: Clock = utc_now,
    ) -> None:
        self._workspaces = workspaces
        self._metadata = metadata
        self._text = text
        self._graph = graph
        self._documents = documents
        self._signal_providers = tuple(signal_providers)
        #: Where a startup's session is recorded. Optional the way `documents`
        #: is: without one a startup still reports the id it minted, and a
        #: later `checkpoint` against it says the session was never opened,
        #: which is true and is the honest answer.
        self._sessions = sessions
        self._now = now

    def assemble(self, request: ContextRequest) -> tuple[ContextPack, ScopeResolution]:
        """Build a pack, and return what resolving its scope had to say.

        The resolution is returned alongside rather than folded in: a marker that
        disagrees with the registry is a fact about the machine, not about the
        pack, and callers that show it -- the CLI, the API -- should not have to
        resolve a second time to find out.
        """
        resolution = self._workspaces.resolve(request.scope)
        # The assembler is handed the scope this found, not a request to find it
        # again. Re-resolving by workspace id would answer with the mapped
        # repository root, so a pack built inside a git worktree would report
        # the main checkout's branch. Resolution happens once, here, and every
        # stage below reads its answer.
        pack = self._assembler(request.depth, _Resolved(resolution.scope)).assemble(
            _with_parsed_terms(request)
        )
        # A startup opens a session; a focused or deep retrieval happens inside
        # one that is already open (core/04 section 34). Minting here rather
        # than in the assembler keeps the assemblers deterministic and puts the
        # one place that knows "this is a startup" in charge of it.
        if request.depth is ContextDepth.STARTUP:
            pack = replace(pack, session=self._open_session(request, resolution, pack))
        return pack, resolution

    def _open_session(
        self, request: ContextRequest, resolution: ScopeResolution, pack: ContextPack
    ) -> SessionId:
        """Mint the session this startup opens, and record it if there is a store.

        Opening it here rather than in a composition root is core/05 section
        14's rule about thin interfaces: a session that the CLI opened and the
        API did not would be business logic living in one client.
        """
        if self._sessions is None:
            return SessionId.new()
        sessions = SessionService(self._sessions, now=self._now)
        session = sessions.open(
            workspace=resolution.scope.workspace_id,
            actor=request.actor or OWNER_ACTOR,
            client=request.client,
        ).id
        # What the session was given from `Context/`, so `wrap` can ask about
        # it and hold a declaration to it (core/04 section 37). Every context
        # item the pack carried, body or reference.
        sessions.record_given_context(session, self._context_documents(pack))
        return session

    def _context_documents(self, pack: ContextPack) -> tuple[StoredDocument, ...]:
        if self._documents is None:
            return ()
        found: list[StoredDocument] = []
        for item in pack.items:
            if item.category != PackCategory.CONTEXT or item.concept_id is None:
                continue
            document = self._documents.get(item.concept_id)
            if document is not None:
                found.append(document)
        return tuple(found)

    # -- internals --------------------------------------------------------

    def _assembler(self, depth: ContextDepth, resolver: ScopeResolver) -> _Assembler:
        match depth:
            case ContextDepth.STARTUP:
                return StartupContextAssembler(
                    resolver=resolver,
                    metadata_index=self._metadata,
                    document_store=self._documents,
                    signal_providers=self._signal_providers,
                    # Only used when core/04 section 16's `--task` names one.
                    text_index=self._text,
                    graph_index=self._graph,
                    now=self._now,
                )
            case ContextDepth.FOCUSED:
                return FocusedContextAssembler(
                    resolver=resolver,
                    metadata_index=self._metadata,
                    text_index=self._text,
                    graph_index=self._graph,
                    document_store=self._documents,
                    signal_providers=self._signal_providers,
                    now=self._now,
                )
            case ContextDepth.DEEP:
                return DeepContextAssembler(
                    resolver=resolver,
                    metadata_index=self._metadata,
                    text_index=self._text,
                    graph_index=self._graph,
                    document_store=self._documents,
                    signal_providers=self._signal_providers,
                    now=self._now,
                )


class _Resolved:
    """A resolver that answers with a scope already found.

    The assemblers take a :class:`~never4ga.context.scope.ScopeResolver` because
    resolution is Stage A of the pipeline and they are entitled to its answer.
    This is that answer, from the one resolution :meth:`ContextService.assemble`
    performs -- the port shape is kept, and the second question is not asked.
    """

    def __init__(self, scope: ResolvedScope) -> None:
        self._scope = scope

    def resolve(self, request: ScopeRequest) -> ResolvedScope:
        return self._scope


def _with_parsed_terms(request: ContextRequest) -> ContextRequest:
    """Terms as `search` reads a query: words, quoted phrases, identifiers.

    Passed through as they arrived, `context focus "indexing scope owner"`
    would send one term holding three words, the lexical engine would quote it
    as a phrase no document contains, and the pack would come back empty. This
    is the parser `search` uses, applied once here so the CLI, the API and the
    MCP server cannot disagree about it.

    Joining before parsing is what `search` does with its arguments, so the two
    commands read the same words the same way. Identifiers and phrases the
    caller named directly come first, and nothing is repeated.
    """
    query = parse_query(" ".join(request.terms))
    return replace(
        request,
        terms=query.terms,
        phrases=tuple(dict.fromkeys((*request.phrases, *query.phrases))),
        exact_identifiers=tuple(
            dict.fromkeys((*request.exact_identifiers, *query.exact_identifiers))
        ),
    )
