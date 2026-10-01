"""Opening a vault for evaluation: real SQLite, real files, no fakes.

The TDD skill's rule -- do not treat a mock as proof that SQLite behaves -- has
more force here than anywhere else in the suite. A retrieval measurement taken
against an in-memory stand-in measures the stand-in.

It assembles a :class:`~never4ga.services.VaultSession`, which is the same thing
the CLI and the API assemble, so the corpus measures the retrieval that ships
rather than a second wiring that resembles it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.adapters.sqlite import (
    SQLiteFTS5Index,
    SQLiteGraphIndex,
    SQLiteIndexState,
    SQLiteMetadataIndex,
    open_index,
)
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.services import VaultSession

__all__ = ["indexed_vault"]


@contextmanager
def indexed_vault(
    vault: Path,
    database: Path,
    *,
    reconcile: bool = True,
) -> Iterator[VaultSession]:
    """Open ``vault`` against ``database``, bringing the index up to date first."""
    documents = FileSystemMarkdownStore(vault)
    manifest = documents.get_by_path(SYSTEM_MANIFEST)
    if manifest is None:
        raise FileNotFoundError(f"{vault} has no {SYSTEM_MANIFEST}; it is not a vault")

    with closing(open_index(database)) as connection:
        session = VaultSession(
            vault_id=manifest.concept_id,
            files=FileSystemVaultFileStore(vault),
            documents=documents,
            metadata=SQLiteMetadataIndex(connection),
            text=SQLiteFTS5Index(connection),
            graph=SQLiteGraphIndex(connection),
            state=SQLiteIndexState(connection),
        )
        if reconcile:
            session.indexer.reconcile()
        yield session
