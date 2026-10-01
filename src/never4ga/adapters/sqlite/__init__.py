"""SQLite projections of the canonical vault.

core/06 section 5 names these as the first implementations of ports that already
exist: ``SQLiteMetadataIndex``, ``SQLiteFTS5Index``, ``SQLiteGraphIndex``. They
are projections and nothing more -- every row here is derived from Markdown and
rebuildable from it (core/06 section 2). `sqlite3` is standard library, so
this package adds no dependency.
"""

from __future__ import annotations

from never4ga.adapters.sqlite.connection import (
    BUSY_TIMEOUT_MS,
    MIGRATIONS,
    SCHEMA_VERSION,
    applied_versions,
    connect,
    migrate,
    open_index,
)
from never4ga.adapters.sqlite.graph_index import SQLiteGraphIndex
from never4ga.adapters.sqlite.index_state import SQLiteIndexState
from never4ga.adapters.sqlite.maintenance_findings import SQLiteMaintenanceFindings
from never4ga.adapters.sqlite.metadata_index import SQLiteMetadataIndex
from never4ga.adapters.sqlite.text_index import SQLiteFTS5Index

__all__ = [
    "BUSY_TIMEOUT_MS",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "SQLiteFTS5Index",
    "SQLiteGraphIndex",
    "SQLiteIndexState",
    "SQLiteMaintenanceFindings",
    "SQLiteMetadataIndex",
    "applied_versions",
    "connect",
    "migrate",
    "open_index",
]
