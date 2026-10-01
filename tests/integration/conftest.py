"""Fixtures the API tests share.

A fixture two test files use belongs here, where pytest looks for it. Importing
one from another test module makes ruff read the name as a redefinition.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.adapters.sqlite import (
    SQLiteFTS5Index,
    SQLiteGraphIndex,
    SQLiteIndexState,
    SQLiteMetadataIndex,
    open_index,
)
from never4ga.api import create_app
from never4ga.domain.identity import ConceptId
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.services import ContentService, SessionFactory, VaultInitializer, VaultSession

CREDENTIAL = "test-credential-not-a-real-one"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents).initialize("Test Vault")
    content = ContentService(files, documents)
    content.create_knowledge(
        "Hybrid Retrieval",
        description="How lexical and vector retrieval combine.",
        domains=["software_development"],
        tags=["search"],
    )
    content.create_workspace("Never4gA", workspace_type="product")
    return root


@pytest.fixture
def sessions(vault: Path, tmp_path: Path) -> SessionFactory:
    database = tmp_path / "index.sqlite3"
    documents = FileSystemMarkdownStore(vault)
    files = FileSystemVaultFileStore(vault)
    vault_id = documents.get_by_path(SYSTEM_MANIFEST).concept_id  # type: ignore[union-attr]

    @contextmanager
    def open_session() -> Iterator[VaultSession]:
        with closing(open_index(database)) as connection:
            yield VaultSession(
                vault_id=vault_id,
                files=files,
                documents=documents,
                metadata=SQLiteMetadataIndex(connection),
                text=SQLiteFTS5Index(connection),
                graph=SQLiteGraphIndex(connection),
                state=SQLiteIndexState(connection),
            )

    return open_session


@pytest.fixture
def vault_id(sessions: SessionFactory) -> str:
    with sessions() as session:
        return str(session.vault_id)


@pytest.fixture
def anonymous(sessions: SessionFactory, vault_id: str) -> Iterator[TestClient]:
    """A client that presents no credential at all."""
    app = create_app(sessions=sessions, credential=CREDENTIAL, vault_id=ConceptId.parse(vault_id))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(sessions: SessionFactory, vault_id: str) -> Iterator[TestClient]:
    app = create_app(sessions=sessions, credential=CREDENTIAL, vault_id=ConceptId.parse(vault_id))
    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = f"Bearer {CREDENTIAL}"
        yield test_client


@pytest.fixture
def indexed(client: TestClient) -> TestClient:
    client.post("/v1/index/reconcile", json={})
    return client
