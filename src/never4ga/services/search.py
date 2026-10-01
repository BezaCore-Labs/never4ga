"""Search, and reading one concept.

Two contracts are kept apart, and this service is the seam between them.

A **candidate** (core/06 section 6) is what a retriever returns: backend-neutral,
built for fusion, carrying no excerpt. A vector backend's notion of a snippet is
not an FTS5 snippet, and a candidate that carried one would stop being
comparable across engines.

A **result** (details/retrieval-context-memory.md section 8) is what a person or
an agent receives: title, excerpt, source lines, authority, staleness, and the
reason it was included.

Excerpts are therefore read from canonical Markdown at request time, never
stored. Chunking is deterministic, so re-chunking the document and matching on
chunk identity finds the exact passage the index matched -- and when it does not,
that is itself the answer: the file has changed since it was indexed, and the
result says so instead of quoting something that is no longer there.

Retrieval order follows core/07: structural metadata narrows the candidate set
(Stage B) *before* the lexical stage runs (Stage D), not after.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from never4ga.context.authority import apply_lifecycle_rules
from never4ga.context.fusion import FusedCandidate, reciprocal_rank_fusion
from never4ga.domain.chunk import LineRange
from never4ga.domain.document import ForeignNote, StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason
from never4ga.domain.retrieval import RetrievalCandidate
from never4ga.errors import StructuredError, VaultPathError
from never4ga.indexing import build_chunks, build_foreign_chunks
from never4ga.layout import SYSTEM_MANIFEST, is_foreign_note, registered_foreign_material
from never4ga.layout.structure import VaultRoot
from never4ga.ports.document_store import DocumentStore, ForeignMaterialStore
from never4ga.ports.graph_index import Direction, GraphIndex, GraphNeighbor
from never4ga.ports.metadata_index import MetadataIndex, MetadataQuery, MetadataRecord
from never4ga.ports.text_index import FIELD_TITLE, TextIndex, TextQuery
from never4ga.schema import is_stale
from never4ga.services.authoring import Clock, utc_now
from never4ga.services.indexing import IndexService

__all__ = [
    "ConceptView",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "foreign_note_at",
    "not_a_concept",
    "parse_query",
]

#: How much of a chunk a result shows. Progressive disclosure
#: (details/retrieval-context-memory.md section 12): a result offers enough to
#: judge relevance, and `concept get` offers the rest.
EXCERPT_CHARACTERS: Final = 320

#: A token worth trying as an exact identifier: it joins letters to digits with
#: a hyphen or a hash, the way `ADR-0002` and `OP#431` do. An ordinary
#: hyphenated word such as `mechanical-first` has no digits and is left alone.
_IDENTIFIER: Final = re.compile(r"^[#\w]*[\w][-#][\w-]*\d[\w-]*$")

_QUOTED: Final = re.compile(r'"([^"]*)"')


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """What a caller asks for, in words plus structural filters."""

    query: str = ""
    types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    workspace_ids: tuple[ConceptId, ...] = ()
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One result, in the shape section 8 asks for.

    Or a note in foreign material (core/02 section 3.3): ``concept_id`` and
    ``concept_type`` are then ``None``, the reason is `foreign_material`, and
    ``path`` is how to reach it. Nothing only a concept has is filled in.
    """

    concept_id: ConceptId | None
    path: VaultPath
    title: str
    concept_type: str | None
    rank: int
    retriever: str
    reason: AcquisitionReason
    heading_path: tuple[str, ...] = ()
    excerpt: str = ""
    line_range: LineRange | None = None
    workspace_id: ConceptId | None = None
    authority: str | None = None
    status: str | None = None
    lifecycle: str | None = None
    verified: Any = None
    is_stale: bool = False
    chunk_has_changed: bool = False
    #: Which retrievers found this, and at what rank each put it. Diagnostic:
    #: it lets a claim that one retriever improves results be checked. This
    #: payload is explicitly unstable while the ranking is tuned.
    lanes: Mapping[str, int] = field(default_factory=dict)
    #: The fused score. Not comparable across queries, and never across
    #: backends (core/06 section 6) -- it explains an ordering, it does not
    #: define one.
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class SearchResponse:
    results: tuple[SearchResult, ...] = ()
    index_is_stale: bool = False


@dataclass(frozen=True, slots=True)
class ConceptView:
    """One concept, canonical content first."""

    document: StoredDocument
    record: MetadataRecord | None = None
    outgoing: tuple[GraphNeighbor, ...] = ()
    incoming: tuple[GraphNeighbor, ...] = ()
    index_is_stale: bool = False


def parse_query(text: str) -> TextQuery:
    """Turn what a person typed into a structured request.

    core/06 section 18 forbids engine syntax as the public API, so this is the
    only place a query string exists, and it produces the same structured
    request the HTTP API and MCP server will send directly.

    A token that looks like an identifier is searched for *both* ways -- as an
    exact identifier and as an ordinary term. A guess about syntax must never
    silently narrow a search to nothing.
    """
    phrases = tuple(phrase.strip() for phrase in _QUOTED.findall(text) if phrase.strip())
    remainder = _QUOTED.sub(" ", text)
    terms = tuple(token for token in remainder.split() if token)
    identifiers = tuple(token for token in terms if _IDENTIFIER.match(token))
    return TextQuery(terms=terms, phrases=phrases, exact_identifiers=identifiers)


class SearchService:
    def __init__(
        self,
        *,
        documents: DocumentStore,
        metadata: MetadataIndex,
        text: TextIndex,
        graph: GraphIndex,
        index: IndexService | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._documents = documents
        self._metadata = metadata
        self._text = text
        self._graph = graph
        self._index = index
        self._clock = clock

    def search(self, request: SearchRequest) -> SearchResponse:
        query = parse_query(request.query)
        stale = self._index.health().is_stale if self._index is not None else False
        if query.is_empty:
            return SearchResponse(index_is_stale=stale)

        scope = self._scope(request)
        if scope is not None and not scope:
            return SearchResponse(index_is_stale=stale)

        lexical = list(
            self._text.search(
                TextQuery(
                    terms=query.terms,
                    phrases=query.phrases,
                    exact_identifiers=query.exact_identifiers,
                    concept_ids=tuple(scope) if scope is not None else (),
                    # Deliberately unlimited here. A limit pushed into one lane
                    # cuts candidates before they have voted, so the result is
                    # the best of a truncated list rather than the best.
                )
            )
        )
        candidates = list(lexical)
        fused = apply_lifecycle_rules(reciprocal_rank_fusion(candidates), self._graph)
        # Before the limit, not after: an archive-heavy query would otherwise
        # spend its ten places on documents it then drops and report a vault
        # with nothing in it.
        fused = tuple(
            item
            for item in fused
            if item.concept_id is None or not self._is_archived(item.concept_id)
        )
        if request.limit is not None:
            fused = fused[: request.limit]

        # A fused result is per owner -- a concept, or a foreign note's path --
        # and the excerpt is per chunk. The lexical lane is the only one that
        # produces a passage a person can read, so its best chunk is what a
        # result shows when it has one.
        passages: dict[str, RetrievalCandidate] = {}
        for candidate in lexical:
            if candidate.chunk is not None:
                passages.setdefault(candidate.chunk.owner, candidate)

        return SearchResponse(
            results=tuple(
                self._result(item, rank, passages.get(item.owner))
                if item.concept_id is not None
                else self._foreign_result(item, rank, passages.get(item.owner))
                for rank, item in enumerate(fused, start=1)
            ),
            index_is_stale=stale,
        )

    def get(self, concept_id: ConceptId) -> ConceptView | None:
        """One concept, read from Markdown; the index only adds to it."""
        # A duplicated identity raises out of the store rather than being
        # answered for: core/02 section 5.1, and picking a winner would hide it.
        document = self._documents.get(concept_id)
        if document is None:
            return None
        return ConceptView(
            document=document,
            record=self._metadata.get(concept_id),
            outgoing=tuple(self._graph.neighbors(concept_id, direction=Direction.OUTGOING)),
            incoming=tuple(self._graph.neighbors(concept_id, direction=Direction.INCOMING)),
            index_is_stale=self._index.health().is_stale if self._index is not None else False,
        )

    # -- internals --------------------------------------------------------

    def _is_archived(self, concept_id: ConceptId) -> bool:
        """Whether this result lives in `90_Archive/`.

        Archived material is excluded from search as well as from Context Packs
        (`core/07` Stage B). Detachment is a property of the material, not of
        the interface reading it; otherwise a query can rank a note about a
        superseded convention above the specification in force.

        By location, for the reason the pack guard is by location: knowledge is
        admitted by type, and archiving a note moves the file without
        restamping it, so the path is the only thing that tells the two apart.
        """
        record = self._metadata.get(concept_id)
        return record is not None and record.path.root == VaultRoot.ARCHIVE

    def _scope(self, request: SearchRequest) -> set[ConceptId] | None:
        """Stage B: narrow by structure before searching text.

        ``None`` means "no structural filter was asked for", which is different
        from "the filter matched nothing" -- the second must return no results
        rather than quietly searching the whole vault.
        """
        if not (request.types or request.tags or request.domains or request.workspace_ids):
            return None
        records = self._metadata.query(
            MetadataQuery(
                types=request.types,
                tags=request.tags,
                domains=request.domains,
                workspace_ids=request.workspace_ids,
            )
        )
        return {record.concept_id for record in records}

    def _result(
        self,
        fused: FusedCandidate,
        rank: int,
        passage: RetrievalCandidate | None,
    ) -> SearchResult:
        candidate = passage or RetrievalCandidate(
            concept_id=fused.concept_id,
            retriever=sorted(fused.contributions)[0],
            rank=rank,
            reason=fused.reasons[0],
        )
        # A concept's result; `_foreign_result` answers for a path.
        concept_id = fused.concept_id
        assert concept_id is not None
        record = self._metadata.get(concept_id)
        document = self._documents.get(concept_id)
        excerpt, line_range, changed = self._passage(document, candidate)
        frontmatter = document.frontmatter if document is not None else {}
        path = (
            record.path
            if record is not None
            else (candidate.source_path or VaultPath.parse("unknown.md"))
        )
        return SearchResult(
            concept_id=concept_id,
            path=document.path if document is not None else path,
            title=_title(record, frontmatter, path),
            concept_type=record.concept_type if record else str(frontmatter.get("type", "unknown")),
            rank=rank,
            retriever=candidate.retriever,
            reason=candidate.reason,
            heading_path=candidate.chunk.heading_path if candidate.chunk else (),
            excerpt=excerpt,
            line_range=line_range,
            workspace_id=record.workspace_id if record else None,
            authority=record.authority if record else None,
            status=record.status if record else None,
            lifecycle=record.lifecycle if record else None,
            verified=frontmatter.get("verified"),
            is_stale=is_stale(frontmatter.get("stale_after"), self._clock()),
            chunk_has_changed=changed,
            lanes=dict(fused.contributions),
            score=fused.score,
        )

    def _foreign_result(
        self,
        fused: FusedCandidate,
        rank: int,
        passage: RetrievalCandidate | None,
    ) -> SearchResult:
        """A note in foreign material, reached by its path (core/02 section 3.3).

        Read from the note as it is now, the way a concept's excerpt is read
        from Markdown. A note gone since it was indexed still answers with the
        path it had and says its passage moved, which is what the index knows
        until the next pass forgets it.
        """
        path = fused.source_path
        assert path is not None
        note = self._foreign_note(path)
        excerpt, line_range, changed = self._foreign_passage(note, passage)
        indexed_title = passage.metadata.get(FIELD_TITLE) if passage is not None else None
        title = (
            note.title
            if note is not None
            else indexed_title
            if isinstance(indexed_title, str) and indexed_title
            else path.name.removesuffix(".md")
        )
        return SearchResult(
            concept_id=None,
            path=path,
            title=title,
            concept_type=None,
            rank=rank,
            retriever=passage.retriever if passage is not None else sorted(fused.contributions)[0],
            reason=fused.reasons[0],
            heading_path=passage.chunk.heading_path if passage and passage.chunk else (),
            excerpt=excerpt,
            line_range=line_range,
            chunk_has_changed=changed,
            lanes=dict(fused.contributions),
            score=fused.score,
        )

    def _foreign_note(self, path: VaultPath) -> ForeignNote | None:
        if not isinstance(self._documents, ForeignMaterialStore):
            return None
        return self._documents.get_foreign_note(path)

    @staticmethod
    def _foreign_passage(
        note: ForeignNote | None, candidate: RetrievalCandidate | None
    ) -> tuple[str, LineRange | None, bool]:
        """As :meth:`_passage`, for a note whose chunks its path owns."""
        if note is None:
            return "", None, True
        chunks = build_foreign_chunks(note)
        if candidate is not None and candidate.chunk is not None:
            for chunk in chunks:
                if chunk.chunk.key == candidate.chunk.key:
                    return _shorten(chunk.text), chunk.line_range, False
        if not chunks:
            return "", None, True
        return _shorten(chunks[0].text), chunks[0].line_range, True

    def _passage(
        self, document: StoredDocument | None, candidate: RetrievalCandidate
    ) -> tuple[str, LineRange | None, bool]:
        """The passage a candidate points at, read from Markdown.

        Chunking is deterministic, so the chunk key identifies the passage
        exactly. A key that no longer exists means the document changed after it
        was indexed; the excerpt falls back to the document's opening and the
        result says the passage moved rather than quoting the wrong lines.
        """
        if document is None or candidate.chunk is None:
            return "", None, False
        for chunk in build_chunks(document):
            if chunk.chunk.key == candidate.chunk.key:
                return _shorten(chunk.text), chunk.line_range, False
        opening = build_chunks(document)
        if not opening:
            return "", None, True
        return _shorten(opening[0].text), opening[0].line_range, True


def foreign_note_at(documents: DocumentStore, raw: str) -> ForeignNote | None:
    """The foreign note ``raw`` names, when it names one (core/02 section 3.3).

    For every surface asked to read a concept by something that is not an
    identity: a path in registered foreign material is not a concept, and the
    right answer is to say so and name `adopt`, not "not found". Anything else
    -- an identity, a concept's path, a directory nobody registered, a string
    that is not a vault path -- is ``None``, and the caller answers as before.
    """
    if not isinstance(documents, ForeignMaterialStore):
        return None
    try:
        path = VaultPath.parse(raw)
    except VaultPathError:
        return None
    manifest = documents.get_by_path(SYSTEM_MANIFEST)
    registered = registered_foreign_material(manifest.frontmatter) if manifest else frozenset()
    if not is_foreign_note(path, registered):
        return None
    return documents.get_foreign_note(path)


def not_a_concept(note: ForeignNote) -> StructuredError:
    """The refusal every surface gives when asked to read a foreign note as a concept."""
    return StructuredError(
        "not_a_concept",
        f"{note.path} is foreign material, not a concept: it has no id, so there "
        "is nothing to read it as beyond the file itself",
        {"path": str(note.path), "title": note.title},
        repair_hint=(
            f"`never4ga adopt {note.path} --type <type>` makes it a concept; until "
            "then open the file, or find it with `never4ga search`"
        ),
    )


def _title(record: MetadataRecord | None, frontmatter: Mapping[str, Any], path: VaultPath) -> str:
    if record is not None:
        return record.title
    title = frontmatter.get("title")
    return title if isinstance(title, str) and title.strip() else path.name.removesuffix(".md")


def _shorten(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT_CHARACTERS:
        return collapsed
    return collapsed[:EXCERPT_CHARACTERS].rstrip() + "…"
