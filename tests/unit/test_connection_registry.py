"""The connection registry -- resolving a named connection from the vault.

Specification:
- core/03 section 16 -- the canonical half lives in the vault; secrets never do.
- core/02 section 21.19 -- `integration` is "canonical documentation/
  configuration describing an integration", located in 50_System/Integrations/.

Each connection is one `integration` concept.
"""

from __future__ import annotations

from typing import Any

import pytest

from never4ga.adapters.fakes.document_store import InMemoryDocumentStore
from never4ga.adapters.fakes.secret_store import InMemorySecretStore
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.errors import ConnectionDefinitionError
from never4ga.services.connections import ConnectionRegistry, secret_ref_for

INTEGRATIONS = "50_System/Integrations"


def _doc(path: str, **frontmatter: Any) -> StoredDocument:
    fm: dict[str, Any] = {
        "type": "integration",
        "schema": "never4ga/0.1",
        "title": "A connection",
        **frontmatter,
    }
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(path),
        frontmatter=fm,
        body="",
    )


def _openproject(name: str = "work_openproject", **extra: Any) -> StoredDocument:
    return _doc(
        f"{INTEGRATIONS}/{name}.md",
        connection=name,
        provider="openproject",
        base_url="https://pm.example.dev",
        project_ref="never4ga",
        **extra,
    )


@pytest.fixture
def store() -> InMemoryDocumentStore:
    return InMemoryDocumentStore()


@pytest.fixture
def registry(store: InMemoryDocumentStore) -> ConnectionRegistry:
    return ConnectionRegistry(documents=store)


class TestResolution:
    def test_resolves_a_connection_by_name(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        store.put(_openproject())
        c = registry.resolve("work_openproject")
        assert c.provider == "openproject"
        assert c.base_url == "https://pm.example.dev"
        assert c.project_ref == "never4ga"

    def test_an_unknown_name_raises_rather_than_returning_none(
        self, registry: ConnectionRegistry
    ) -> None:
        # Nothing guesses. A missing connection is a configuration error the
        # caller must see, not an empty result it might ignore.
        with pytest.raises(ConnectionDefinitionError, match="nowhere"):
            registry.resolve("nowhere")

    def test_lists_every_connection(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        store.put(_openproject("home"))
        store.put(_openproject("work"))
        assert {c.name for c in registry.list_connections()} == {"home", "work"}

    def test_ignores_documents_that_are_not_integrations(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        store.put(_doc("30_Knowledge/Notes/a-note.md", type="note", connection="decoy"))
        assert registry.list_connections() == ()

    def test_an_integration_without_a_connection_key_is_not_a_connection(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        # core/02 registers `integration` for documenting an integration
        # generally. A page describing one is not a connection definition.
        store.put(_doc(f"{INTEGRATIONS}/notes.md", provider="openproject"))
        assert registry.list_connections() == ()


class TestSecretsNeverEnterTheVault:
    @pytest.mark.parametrize(
        "key", ["api_token", "password", "oauth_refresh_token", "webhook_secret"]
    )
    def test_a_forbidden_key_is_refused(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry, key: str
    ) -> None:
        store.put(_openproject(**{key: "s3cret"}))
        with pytest.raises(ConnectionDefinitionError, match=key):
            registry.resolve("work_openproject")

    def test_the_error_does_not_repeat_the_secret(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        # Refusing a leaked secret by printing it into a log is not refusing it.
        store.put(_openproject(api_token="hunter2-do-not-print"))
        with pytest.raises(ConnectionDefinitionError) as excinfo:
            registry.resolve("work_openproject")
        assert "hunter2-do-not-print" not in str(excinfo.value)

    def test_findings_report_rather_than_repair(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        # core/02: validation always reports, never repairs. The document is
        # left exactly as it was.
        store.put(_openproject(api_token="s3cret"))
        findings = registry.findings()
        assert len(findings) == 1
        assert "api_token" in findings[0].detail
        assert "s3cret" not in findings[0].detail
        doc = store.get_by_path(VaultPath.parse(f"{INTEGRATIONS}/work_openproject.md"))
        assert doc is not None
        assert doc.frontmatter["api_token"] == "s3cret"

    def test_a_healthy_vault_reports_nothing(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        store.put(_openproject())
        assert registry.findings() == ()


class TestSecretRef:
    def test_the_token_is_fetched_by_name_from_the_store(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        store.put(_openproject())
        secrets = InMemorySecretStore()
        connection = registry.resolve("work_openproject")
        secrets.set(secret_ref_for(connection), "the-token")
        assert secrets.get(secret_ref_for(connection)) == "the-token"

    def test_the_ref_carries_no_value_field(
        self, store: InMemoryDocumentStore, registry: ConnectionRegistry
    ) -> None:
        store.put(_openproject())
        ref = secret_ref_for(registry.resolve("work_openproject"))
        assert not hasattr(ref, "value")
