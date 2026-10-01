"""FTS5 behaviour the port contract cannot express.

The contract says what any lexical engine must do. This says what must be true
of *this* engine: that its syntax never leaks into a request (core/06 section 25
test G), and that the field weighting it offers actually applies.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import pytest

from never4ga.adapters.sqlite import SQLiteFTS5Index, open_index
from never4ga.domain.identity import ConceptId
from never4ga.ports.text_index import FIELD_KEYWORDS, FIELD_TITLE, IndexedChunk, TextQuery
from tests.contracts.text_index_contract import make_chunk


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(open_index(tmp_path / "index.sqlite3")) as open_connection:
        yield open_connection


@pytest.fixture
def index(connection: sqlite3.Connection) -> SQLiteFTS5Index:
    return SQLiteFTS5Index(connection)


def with_fields(
    text: str, *, title: str = "", keywords: str = "", path: str = "30_Knowledge/Notes/a.md"
) -> IndexedChunk:
    chunk = make_chunk(text, path=path)
    return IndexedChunk(
        chunk=chunk.chunk,
        path=chunk.path,
        text=chunk.text,
        line_range=chunk.line_range,
        metadata={FIELD_TITLE: title, FIELD_KEYWORDS: keywords},
    )


class TestNoEngineSyntaxLeaks:
    """A caller writes structured requests. Nothing it can send may misbehave."""

    @pytest.mark.parametrize("term", ["OR", "AND", "NOT", "NEAR"])
    def test_an_operator_word_is_searched_for_literally(
        self, index: SQLiteFTS5Index, term: str
    ) -> None:
        index.index_chunk(make_chunk(f"the word {term.casefold()} appears here"))
        assert index.search(TextQuery(terms=(term,)))

    @pytest.mark.parametrize(
        "term", ['a"quote', "paren(these)", "colon:separated", "star*", "^caret"]
    )
    def test_punctuation_neither_raises_nor_injects(
        self, index: SQLiteFTS5Index, term: str
    ) -> None:
        index.index_chunk(make_chunk("workspace resolution"))
        assert index.search(TextQuery(terms=(term,))) == ()

    def test_a_trailing_star_does_not_become_a_prefix_search(self, index: SQLiteFTS5Index) -> None:
        # Silent prefix matching would make results depend on punctuation the
        # caller did not intend as syntax.
        index.index_chunk(make_chunk("workspace resolution"))
        assert index.search(TextQuery(terms=("work*",))) == ()

    def test_an_empty_term_is_ignored_rather_than_matching_everything(
        self, index: SQLiteFTS5Index
    ) -> None:
        index.index_chunk(make_chunk("workspace resolution"))
        assert index.search(TextQuery(terms=("   ",))) == ()


class TestFieldWeighting:
    def test_a_title_hit_outranks_a_body_hit(self, index: SQLiteFTS5Index) -> None:
        index.index_chunk(
            with_fields("some unrelated prose", title="Mechanical context", path="a.md")
        )
        index.index_chunk(with_fields("mechanical appears once here", title="Other", path="b.md"))
        (first, _second) = index.search(TextQuery(terms=("mechanical",)))
        assert str(first.source_path) == "a.md"

    def test_the_heading_path_is_searchable(self, index: SQLiteFTS5Index) -> None:
        index.index_chunk(make_chunk("body text", heading_path=("Retrieval", "Fusion")))
        assert index.search(TextQuery(terms=("fusion",)))

    def test_keywords_are_searchable(self, index: SQLiteFTS5Index) -> None:
        # Tags and aliases reach the index this way rather than through prose.
        index.index_chunk(with_fields("body text", keywords="architecture adr-index"))
        assert index.search(TextQuery(terms=("architecture",)))


class TestStaleness:
    def test_reindexing_an_edited_chunk_leaves_no_stale_hit(self, index: SQLiteFTS5Index) -> None:
        concept_id = ConceptId.new()
        original = make_chunk("obsolete wording", concept_id=concept_id)
        index.index_chunk(original)
        revised = IndexedChunk(
            chunk=original.chunk,
            path=original.path,
            text="current wording",
            line_range=original.line_range,
        )
        index.index_chunk(revised)
        assert index.search(TextQuery(terms=("obsolete",))) == ()
        assert index.search(TextQuery(terms=("current",)))

    def test_clearing_leaves_no_orphaned_index_rows(
        self, index: SQLiteFTS5Index, connection: sqlite3.Connection
    ) -> None:
        index.index_chunk(make_chunk("workspace"))
        index.clear()
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0


class TestDeletesNeverScanTheFtsTable:
    """Every delete from `chunks_fts` addresses the row by rowid.

    `chunk_id` is UNINDEXED in `chunks_fts`, so a delete addressed by it is a
    full scan of the virtual table. One scan per chunk written would make a
    full reconcile O(chunks squared) while the write lock is held. FTS5 plans a
    rowid delete as `INDEX 0:=` rather than a bare `INDEX 0:`.
    """

    def _fts_delete_plans(
        self, connection: sqlite3.Connection, act: Callable[[], None]
    ) -> list[tuple[str, str]]:
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        try:
            act()
        finally:
            connection.set_trace_callback(None)
        plans = []
        for statement in statements:
            if "chunks_fts" not in statement or not statement.lstrip().upper().startswith("DELETE"):
                continue
            for row in connection.execute(f"EXPLAIN QUERY PLAN {statement}"):
                detail = row["detail"]
                if "chunks_fts" in detail:
                    plans.append((statement.strip(), detail))
        return plans

    def test_reindexing_a_chunk_deletes_by_rowid(
        self, index: SQLiteFTS5Index, connection: sqlite3.Connection
    ) -> None:
        chunk = make_chunk("first wording")
        index.index_chunk(chunk)
        plans = self._fts_delete_plans(connection, lambda: index.index_chunk(chunk))
        assert plans, "reindexing must delete the stale FTS row"
        for statement, detail in plans:
            assert not detail.rstrip().endswith("INDEX 0:"), (statement, detail)

    def test_removing_a_document_deletes_by_rowid(
        self, index: SQLiteFTS5Index, connection: sqlite3.Connection
    ) -> None:
        concept_id = ConceptId.new()
        for ordinal in range(3):
            index.index_chunk(
                make_chunk(f"chunk {ordinal}", concept_id=concept_id, ordinal=ordinal)
            )
        plans = self._fts_delete_plans(connection, lambda: index.remove_document(concept_id))
        assert plans, "removal must delete the FTS rows"
        for statement, detail in plans:
            assert not detail.rstrip().endswith("INDEX 0:"), (statement, detail)
