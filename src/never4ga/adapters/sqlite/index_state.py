"""SQLiteIndexState -- the index's memory of its own work.

`details/data-indexing-maintenance.md` section 3 puts ``content_hash``,
``file_size``, ``filesystem_mtime`` and ``indexed_at`` on the ``documents`` row
rather than in a table of their own, so that is where they live. One document,
one row, whichever concern is writing.

That makes ``documents`` shared with :class:`SQLiteMetadataIndex`, and the
division is by column: the metadata projection owns what frontmatter says, this
owns what the last indexing run observed. Each upserts only its own columns, so
neither clobbers the other and the order they run in does not matter.

The one consequence worth knowing: clearing the metadata projection drops the
rows, and with them this record. That is correct for the only caller that clears
-- ``rebuild``, which discards every projection together and rebuilds them all
from Markdown -- and it is why nothing else clears one projection alone.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.index_state import IndexedDocument, IndexedPath, LinkRecord

__all__ = ["SQLiteIndexState"]

_RECORD: Final = """
INSERT INTO documents (id, path, type, title, content_hash, indexed_at, file_size, filesystem_mtime)
VALUES (?, ?, 'unknown', ?, ?, ?, ?, ?)
ON CONFLICT (id) DO UPDATE SET
    path             = excluded.path,
    content_hash     = excluded.content_hash,
    indexed_at       = excluded.indexed_at,
    file_size        = excluded.file_size,
    filesystem_mtime = excluded.filesystem_mtime
"""

_SELECT: Final = """
SELECT id, path, content_hash, indexed_at, file_size, filesystem_mtime
FROM documents
WHERE content_hash IS NOT NULL
"""


class SQLiteIndexState:
    """An IndexState over the ``documents`` bookkeeping columns and ``links``."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # -- documents --------------------------------------------------------

    def record(self, document: IndexedDocument) -> None:
        with self._connection:
            self._connection.execute(
                _RECORD,
                (
                    str(document.concept_id),
                    str(document.path),
                    # Only used when this is the first thing to see the document;
                    # the metadata projection overwrites it with the real title.
                    document.path.name.removesuffix(".md"),
                    document.content_hash,
                    document.indexed_at,
                    document.file_size,
                    document.modified_at,
                ),
            )

    def forget(self, concept_id: ConceptId) -> None:
        document_id = str(concept_id)
        with self._connection:
            self._connection.execute(
                "DELETE FROM links WHERE source_document_id = ?", (document_id,)
            )
            self._connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def get(self, concept_id: ConceptId) -> IndexedDocument | None:
        row = self._connection.execute(f"{_SELECT} AND id = ?", (str(concept_id),)).fetchone()
        return _indexed(row) if row is not None else None

    def indexed_documents(self) -> Sequence[IndexedDocument]:
        rows = self._connection.execute(f"{_SELECT} ORDER BY path, id")
        return tuple(_indexed(row) for row in rows)

    # -- foreign notes (core/02 section 3.3) -------------------------------

    def record_path(self, record: IndexedPath) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO indexed_paths
                    (path, content_hash, indexed_at, file_size, filesystem_mtime)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (path) DO UPDATE SET
                    content_hash     = excluded.content_hash,
                    indexed_at       = excluded.indexed_at,
                    file_size        = excluded.file_size,
                    filesystem_mtime = excluded.filesystem_mtime
                """,
                (
                    str(record.path),
                    record.content_hash,
                    record.indexed_at,
                    record.file_size,
                    record.modified_at,
                ),
            )

    def forget_path(self, path: VaultPath) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM indexed_paths WHERE path = ?", (str(path),))

    def indexed_paths(self) -> Sequence[IndexedPath]:
        rows = self._connection.execute(
            """
            SELECT path, content_hash, indexed_at, file_size, filesystem_mtime
            FROM indexed_paths
            """
        )
        # Ordered here rather than by SQL: a path's order is its segments',
        # and `ORDER BY path` would put `a-b/c.md` before `a/c.md`.
        return tuple(sorted((_indexed_path(row) for row in rows), key=lambda r: r.path))

    # -- links ------------------------------------------------------------

    def replace_links(self, source: ConceptId, links: Sequence[LinkRecord]) -> None:
        document_id = str(source)
        with self._connection:
            self._connection.execute(
                "DELETE FROM links WHERE source_document_id = ?", (document_id,)
            )
            self._connection.executemany(
                """
                INSERT INTO links (
                    source_document_id, target_path, target_name,
                    target_document_id, anchor, resolved
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document_id,
                        str(link.target_path) if link.target_path is not None else None,
                        link.target_name,
                        str(link.target_id) if link.target_id else None,
                        link.anchor,
                        int(link.target_id is not None),
                    )
                    for link in links
                ],
            )

    def links(self) -> Sequence[LinkRecord]:
        rows = self._connection.execute(
            """
            SELECT source_document_id, target_path, target_name,
                   target_document_id, anchor, resolved
            FROM links
            ORDER BY source_document_id, target_path, target_name, anchor
            """
        )
        return tuple(_link(row) for row in rows)

    def clear(self) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM links")
            self._connection.execute("DELETE FROM indexed_paths")
            self._connection.execute(
                """
                UPDATE documents
                SET content_hash = NULL, indexed_at = NULL,
                    file_size = NULL, filesystem_mtime = NULL
                """
            )


def _indexed(row: sqlite3.Row) -> IndexedDocument:
    return IndexedDocument(
        concept_id=ConceptId.parse(row["id"]),
        path=VaultPath.parse(row["path"]),
        content_hash=row["content_hash"],
        indexed_at=row["indexed_at"],
        file_size=row["file_size"],
        modified_at=row["filesystem_mtime"],
    )


def _indexed_path(row: sqlite3.Row) -> IndexedPath:
    return IndexedPath(
        path=VaultPath.parse(row["path"]),
        content_hash=row["content_hash"],
        indexed_at=row["indexed_at"],
        file_size=row["file_size"],
        modified_at=row["filesystem_mtime"],
    )


def _link(row: sqlite3.Row) -> LinkRecord:
    target_id = row["target_document_id"]
    stored_path = row["target_path"]
    return LinkRecord(
        source=ConceptId.parse(row["source_document_id"]),
        target_path=VaultPath.parse(stored_path) if stored_path else None,
        target_name=row["target_name"],
        target_id=ConceptId.parse(target_id) if target_id else None,
        anchor=row["anchor"],
    )
