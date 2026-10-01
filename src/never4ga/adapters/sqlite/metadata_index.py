"""SQLiteMetadataIndex -- the frontmatter projection (core/07 Stage B).

Structural metadata narrows the candidate set before any search runs, which is
the cheapest stage of mechanical acquisition and the one that keeps every later
stage bounded (core/07 section 6).

Two rules govern what this adapter is allowed to be.

**No caller writes SQL.** core/06 section 25 test G: a public request carries a
:class:`~never4ga.ports.metadata_index.MetadataQuery`, and rendering it into
parameterised SQL happens here and nowhere else. Swapping in a PostgreSQL or
DuckDB projection must not reach a single caller.

**Unknown fields survive.** core/00 #15 and core/02 section 20 require extension
metadata to be preserved. Anything Never4gA does not model natively lands in
``document_metadata`` as JSON rather than forcing a schema migration
(`details/data-indexing-maintenance.md` section 3).

This adapter owns the columns the port models. The bookkeeping columns of
``documents`` -- ``content_hash``, ``file_size``, ``filesystem_mtime``,
``indexed_at``, ``validation_state`` -- describe an *indexing run* rather than a
concept, and belong to the indexing service. An upsert here leaves them alone.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any, Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.metadata_index import MetadataQuery, MetadataRecord

__all__ = ["SQLiteMetadataIndex"]

_COLUMNS: Final = (
    "id",
    "path",
    "type",
    "title",
    "workspace_id",
    "lifecycle",
    "status",
    "authority",
)

_UPSERT: Final = f"""
INSERT INTO documents ({", ".join(_COLUMNS)})
VALUES ({", ".join("?" * len(_COLUMNS))})
ON CONFLICT (id) DO UPDATE SET
    {", ".join(f"{column} = excluded.{column}" for column in _COLUMNS if column != "id")}
"""

#: Each entry maps a query field to the column it filters, so adding a filter is
#: a line here rather than a new branch in the query builder.
_SCALAR_FILTERS: Final = (
    ("types", "type"),
    ("workspace_ids", "workspace_id"),
    ("lifecycles", "lifecycle"),
    ("statuses", "status"),
    ("authorities", "authority"),
)

#: Values within one field are alternatives; every populated field narrows.
_SET_FILTERS: Final = (
    ("tags", "document_tags", "tag"),
    ("domains", "document_domains", "domain"),
)


class SQLiteMetadataIndex:
    """A MetadataIndex over the ``documents`` projection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # -- writing ----------------------------------------------------------

    def upsert(self, record: MetadataRecord) -> None:
        with self._connection:
            self._connection.execute(
                _UPSERT,
                (
                    str(record.concept_id),
                    str(record.path),
                    record.concept_type,
                    record.title,
                    str(record.workspace_id) if record.workspace_id else None,
                    record.lifecycle,
                    record.status,
                    record.authority,
                ),
            )
            self._replace_vocabulary(record, "tags", "document_tags", "tag", record.tags)
            self._replace_vocabulary(
                record, "domains", "document_domains", "domain", record.domains
            )
            self._replace_extra(record)

    def remove(self, concept_id: ConceptId) -> None:
        with self._connection:
            # Tags, domains and extension metadata cascade (foreign_keys = ON).
            self._connection.execute("DELETE FROM documents WHERE id = ?", (str(concept_id),))

    def clear(self) -> None:
        """Discard the projection. core/06 section 2: it must be disposable."""
        with self._connection:
            for table in ("document_tags", "document_domains", "document_metadata", "documents"):
                self._connection.execute(f"DELETE FROM {table}")
            # The vocabularies are projections of the documents that used them.
            for table in ("tags", "domains"):
                self._connection.execute(f"DELETE FROM {table}")

    # -- reading ----------------------------------------------------------

    def get(self, concept_id: ConceptId) -> MetadataRecord | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE id = ?", (str(concept_id),)
        ).fetchone()
        return self._record(row) if row is not None else None

    def query(self, query: MetadataQuery) -> Sequence[MetadataRecord]:
        sql, parameters = _render(query)
        rows = self._connection.execute(sql, parameters).fetchall()
        return tuple(self._record(row) for row in rows)

    # -- internals --------------------------------------------------------

    def _replace_vocabulary(
        self,
        record: MetadataRecord,
        vocabulary: str,
        junction: str,
        column: str,
        values: tuple[str, ...],
    ) -> None:
        document_id = str(record.concept_id)
        self._connection.execute(f"DELETE FROM {junction} WHERE document_id = ?", (document_id,))
        for value in values:
            self._connection.execute(
                f"INSERT OR IGNORE INTO {vocabulary} ({column}) VALUES (?)", (value,)
            )
            self._connection.execute(
                f"INSERT OR IGNORE INTO {junction} (document_id, {column}) VALUES (?, ?)",
                (document_id, value),
            )

    def _replace_extra(self, record: MetadataRecord) -> None:
        document_id = str(record.concept_id)
        self._connection.execute(
            "DELETE FROM document_metadata WHERE document_id = ?", (document_id,)
        )
        for key, value in record.extra.items():
            self._connection.execute(
                "INSERT INTO document_metadata (document_id, key, value_json) VALUES (?, ?, ?)",
                (document_id, key, _encode(value)),
            )

    def _record(self, row: sqlite3.Row) -> MetadataRecord:
        document_id = row["id"]
        return MetadataRecord(
            concept_id=ConceptId.parse(document_id),
            concept_type=row["type"],
            path=VaultPath.parse(row["path"]),
            title=row["title"],
            workspace_id=ConceptId.parse(row["workspace_id"]) if row["workspace_id"] else None,
            tags=self._vocabulary("document_tags", "tag", document_id),
            domains=self._vocabulary("document_domains", "domain", document_id),
            lifecycle=row["lifecycle"],
            status=row["status"],
            authority=row["authority"],
            extra=self._extra(document_id),
        )

    def _vocabulary(self, junction: str, column: str, document_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            f"SELECT {column} FROM {junction} WHERE document_id = ? ORDER BY {column}",
            (document_id,),
        )
        return tuple(row[column] for row in rows)

    def _extra(self, document_id: str) -> dict[str, Any]:
        rows = self._connection.execute(
            "SELECT key, value_json FROM document_metadata WHERE document_id = ? ORDER BY key",
            (document_id,),
        )
        return {row["key"]: json.loads(row["value_json"]) for row in rows}


def _render(query: MetadataQuery) -> tuple[str, tuple[Any, ...]]:
    """Turn a structured filter into parameterised SQL.

    Every populated field narrows the result set (AND); values within one field
    are alternatives (any-of). An empty query matches everything.
    """
    clauses: list[str] = []
    parameters: list[Any] = []

    for field, column in _SCALAR_FILTERS:
        values = getattr(query, field)
        if not values:
            continue
        clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
        parameters.extend(str(value) for value in values)

    for field, junction, column in _SET_FILTERS:
        values = getattr(query, field)
        if not values:
            continue
        clauses.append(
            f"id IN (SELECT document_id FROM {junction} "
            f"WHERE {column} IN ({', '.join('?' * len(values))}))"
        )
        parameters.extend(values)

    sql = "SELECT * FROM documents"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # Deterministic order: the projection must answer the same way twice.
    sql += " ORDER BY path, id"
    if query.limit is not None:
        sql += " LIMIT ?"
        parameters.append(query.limit)
    return sql, tuple(parameters)


def _encode(value: Any) -> str:
    """JSON for the projection, which is derived; Markdown stays canonical.

    A frontmatter value that JSON cannot represent -- a date, say -- is recorded
    as its string form rather than refused. Losing a type in a rebuildable
    projection is acceptable; failing to index the document is not.
    """
    return json.dumps(value, default=str)
