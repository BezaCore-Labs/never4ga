"""SQLiteFTS5Index -- lexical retrieval (core/07 Stage D, core/05 section 21).

FTS5 is the v0.1 lexical engine, and core/06 section 18 is explicit that its
syntax must not become Never4gA's public API. A caller sends a
:class:`~never4ga.ports.text_index.TextQuery` with separate term, phrase and
exact-identifier fields; turning that into ``MATCH`` syntax happens here, and a
PostgreSQL FTS or OpenSearch adapter would turn the same request into its own.

Two behaviours are worth stating because they are easy to get subtly wrong.

**Exact identifiers never match substrings.** core/07 section 16's "exact
decision" test: asking for ``ADR-0002`` must not return ``ADR-00021``. The
default tokenizer splits both on the hyphen, so a phrase query already
distinguishes them -- but the guarantee must not rest on a tokenizer setting
that a future migration could change. Every candidate is re-checked against the
stored text with a word-boundary match before it is returned.

**Exact identifiers outrank term matches.** An identifier is a request for one
specific thing; term relevance is a guess. The two are collected separately and
concatenated, rather than mixed into one BM25 ordering where a chunk that says
"adr" five times could displace the decision actually asked for.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from typing import Any, Final

from never4ga.domain.capabilities import TextIndexCapability
from never4ga.domain.chunk import ChunkIdentity
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.retrieval import RetrievalCandidate
from never4ga.ports.text_index import FIELD_KEYWORDS, FIELD_TITLE, IndexedChunk, TextQuery

__all__ = ["SQLiteFTS5Index"]

RETRIEVER: Final = "text"

#: BM25 weights, in the column order ``chunks_fts`` declares. A hit in the
#: document's title says more about relevance than a hit in its prose, and a hit
#: in the heading path more than one in the body beneath it. ``chunk_id`` is
#: UNINDEXED and weighted zero.
_WEIGHTS: Final = (0.0, 5.0, 2.0, 1.0, 3.0)

_SEARCH: Final = f"""
SELECT c.chunk_id      AS chunk_id,
       c.document_id   AS document_id,
       c.path          AS path,
       c.ordinal       AS ordinal,
       c.heading_path  AS heading_path,
       c.text          AS text,
       c.content_hash  AS content_hash,
       c.policy_version AS policy_version,
       c.start_line    AS start_line,
       c.end_line      AS end_line,
       c.metadata_json AS metadata_json,
       bm25(chunks_fts, {", ".join(str(weight) for weight in _WEIGHTS)}) AS score
FROM chunks_fts
JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
WHERE chunks_fts MATCH ?
"""

#: The separator between heading path segments in storage. A record separator
#: keeps a heading containing "/" or ">" from inventing a level.
_SEPARATOR: Final = "\x1e"


class SQLiteFTS5Index:
    """A TextIndex over SQLite FTS5."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def capabilities(self) -> frozenset[TextIndexCapability]:
        return frozenset(
            {
                TextIndexCapability.EXACT_IDENTIFIERS,
                TextIndexCapability.PHRASE_TERMS,
                TextIndexCapability.FIELD_WEIGHTS,
                TextIndexCapability.SCOPED_FILTERING,
            }
        )

    # -- writing ----------------------------------------------------------

    def index_chunk(self, chunk: IndexedChunk) -> None:
        """Add or replace one chunk. Re-indexing the same chunk must not duplicate it.

        The stale FTS row is deleted **by rowid**, never by ``chunk_id`` --
        that column is UNINDEXED, so a delete addressed by it is a full scan
        of the virtual table, and one scan per chunk written makes indexing
        O(chunks²). ``chunks`` remembers the rowid at insert precisely so this
        delete can seek.
        """
        key = chunk.chunk.key
        heading_path = _SEPARATOR.join(chunk.chunk.heading_path)
        with self._connection:
            stale = self._connection.execute(
                "SELECT fts_rowid FROM chunks WHERE chunk_id = ?", (key,)
            ).fetchone()
            if stale is not None:
                if stale["fts_rowid"] is not None:
                    self._connection.execute(
                        "DELETE FROM chunks_fts WHERE rowid = ?", (stale["fts_rowid"],)
                    )
                else:
                    # A row from before migration 7 whose backfill found no FTS
                    # row to map. The slow form once, for a case a rebuilt or
                    # migrated database never presents.
                    self._connection.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (key,))
                self._connection.execute("DELETE FROM chunks WHERE chunk_id = ?", (key,))
            cursor = self._connection.execute(
                """
                INSERT INTO chunks_fts (chunk_id, title, heading_path, text, keywords)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    key,
                    _text_field(chunk.metadata.get(FIELD_TITLE)),
                    " ".join(chunk.chunk.heading_path),
                    chunk.text,
                    _text_field(chunk.metadata.get(FIELD_KEYWORDS)),
                ),
            )
            self._connection.execute(
                """
                INSERT INTO chunks (
                    chunk_id, document_id, path, ordinal, heading_path, text,
                    content_hash, policy_version, start_line, end_line, metadata_json,
                    fts_rowid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    # NULL for a path-owned chunk from a foreign note (core/02
                    # section 3.3): the row's owner is then its path, and no
                    # concept query can reach it.
                    None if chunk.chunk.concept_id is None else str(chunk.chunk.concept_id),
                    str(chunk.path),
                    chunk.chunk.ordinal,
                    heading_path,
                    chunk.text,
                    chunk.chunk.content_hash,
                    chunk.chunk.policy_version,
                    chunk.line_range.start,
                    chunk.line_range.end,
                    json.dumps(dict(chunk.metadata), default=str),
                    cursor.lastrowid,
                ),
            )

    def chunk_keys(self) -> frozenset[str]:
        rows = self._connection.execute("SELECT chunk_id FROM chunks")
        return frozenset(str(row["chunk_id"]) for row in rows)

    def remove_document(self, concept_id: ConceptId) -> None:
        """Remove every chunk of one document, addressing FTS rows by rowid.

        A pre-migration-7 row whose ``fts_rowid`` is NULL would leave its FTS
        row behind; the search JOIN already makes such an orphan invisible,
        and a rebuild removes it. The alternative -- a per-document full scan
        of the virtual table -- is quadratic over a full reindex.
        """
        document_id = str(concept_id)
        with self._connection:
            self._connection.execute(
                """
                DELETE FROM chunks_fts WHERE rowid IN (
                    SELECT fts_rowid FROM chunks
                    WHERE document_id = ? AND fts_rowid IS NOT NULL
                )
                """,
                (document_id,),
            )
            self._connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))

    def remove_path(self, path: VaultPath) -> None:
        """Remove the chunks a path owns: foreign-note rows with no document."""
        with self._connection:
            self._connection.execute(
                """
                DELETE FROM chunks_fts WHERE rowid IN (
                    SELECT fts_rowid FROM chunks
                    WHERE document_id IS NULL AND path = ? AND fts_rowid IS NOT NULL
                )
                """,
                (str(path),),
            )
            self._connection.execute(
                "DELETE FROM chunks WHERE document_id IS NULL AND path = ?", (str(path),)
            )

    def clear(self) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM chunks_fts")
            self._connection.execute("DELETE FROM chunks")

    # -- reading ----------------------------------------------------------

    def search(self, query: TextQuery) -> Sequence[RetrievalCandidate]:
        if query.is_empty:
            return ()

        scope = tuple(str(concept_id) for concept_id in query.concept_ids)
        paths = query.include_paths
        ordered: list[tuple[sqlite3.Row, ReasonCode]] = []
        seen: set[str] = set()

        # Exact identifiers first, and separately: see the module docstring.
        for row in self._matching(_identifier_expression(query.exact_identifiers), scope, paths):
            if _mentions_all(row["text"], query.exact_identifiers) and row["chunk_id"] not in seen:
                seen.add(row["chunk_id"])
                ordered.append((row, ReasonCode.EXACT_IDENTIFIER_MATCH))

        for row in self._matching(_term_expression(query), scope, paths):
            if row["chunk_id"] not in seen:
                seen.add(row["chunk_id"])
                ordered.append((row, ReasonCode.FTS_RANK))

        if query.limit is not None:
            ordered = ordered[: query.limit]
        return tuple(
            _candidate(row, code, rank) for rank, (row, code) in enumerate(ordered, start=1)
        )

    def _matching(
        self, expression: str, scope: tuple[str, ...], include_paths: bool
    ) -> list[sqlite3.Row]:
        if not expression:
            return []
        sql, parameters = _SEARCH, [expression]
        if scope:
            within = f"c.document_id IN ({', '.join('?' * len(scope))})"
            # A path-owned chunk has no document_id.
            sql += (
                f" AND ({within} OR c.document_id IS NULL)" if include_paths else f" AND {within}"
            )
            parameters.extend(scope)
        # BM25 returns a smaller (more negative) score for a better match; the
        # chunk key breaks ties so two runs never disagree.
        sql += " ORDER BY score, c.chunk_id"
        try:
            return list(self._connection.execute(sql, parameters))
        except sqlite3.OperationalError:
            # A malformed MATCH expression is a bad request, not a broken index.
            # Callers send structured queries; nothing they can write should
            # raise, so an unparseable expression means no results.
            return []


def _candidate(row: sqlite3.Row, code: ReasonCode, rank: int) -> RetrievalCandidate:
    heading_path = tuple(part for part in row["heading_path"].split(_SEPARATOR) if part)
    owner = row["document_id"]
    concept_id = None if owner is None else ConceptId.parse(owner)
    path = VaultPath.parse(row["path"])
    return RetrievalCandidate(
        concept_id=concept_id,
        retriever=RETRIEVER,
        rank=rank,
        reason=AcquisitionReason.of(code, detail=f"rank {rank}"),
        chunk=ChunkIdentity(
            concept_id=concept_id,
            source=None if concept_id is not None else path,
            heading_path=heading_path,
            ordinal=int(row["ordinal"]),
            content_hash=row["content_hash"],
            policy_version=row["policy_version"],
        ),
        source_path=path,
        provider_score=float(row["score"]),
        metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
    )


def _term_expression(query: TextQuery) -> str:
    """Terms and phrases, any of which may match; BM25 ranks the rest."""
    parts = [_quote(term) for term in query.terms]
    parts.extend(_quote(phrase) for phrase in query.phrases)
    return " OR ".join(part for part in parts if part)


def _identifier_expression(identifiers: tuple[str, ...]) -> str:
    """Each identifier as a phrase, so its tokens must be adjacent and in order."""
    return " OR ".join(part for part in (_quote(identifier) for identifier in identifiers) if part)


def _quote(value: str) -> str:
    """One FTS5 string literal. Doubling the quote is the whole escape rule."""
    cleaned = value.strip()
    if not cleaned:
        return ""
    return '"' + cleaned.replace('"', '""') + '"'


def _mentions_all(text: str, identifiers: tuple[str, ...]) -> bool:
    """Whether the text contains any identifier as a whole token.

    The guarantee core/07 section 16 asks for, enforced independently of the
    tokenizer: ``ADR-0002`` must not be found inside ``ADR-00021``.
    """
    return any(
        re.search(rf"(?<![\w-]){re.escape(identifier.strip())}(?![\w-])", text, re.IGNORECASE)
        for identifier in identifiers
        if identifier.strip()
    )


def _text_field(value: Any) -> str:
    """Whatever the pipeline put in an optional field, as searchable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return " ".join(str(item) for item in value)
    return str(value)
