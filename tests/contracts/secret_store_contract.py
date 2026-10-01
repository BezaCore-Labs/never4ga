"""SecretStore contract.

Specification:
- core/05 section 9: secrets are never stored in committed Markdown, and never
  enter the Git-backed vault.
- details/security-configuration.md.
"""

from __future__ import annotations

import pytest

from never4ga.ports.secret_store import SecretRef, SecretStore


class SecretStoreContract:
    @pytest.fixture
    def store(self) -> SecretStore:
        raise NotImplementedError("supply a SecretStore fixture")

    def test_set_then_get(self, store: SecretStore) -> None:
        ref = SecretRef("openproject.api_token")
        store.set(ref, "s3cret")
        assert store.get(ref) == "s3cret"

    def test_get_missing_returns_none(self, store: SecretStore) -> None:
        assert store.get(SecretRef("nothing.here")) is None

    def test_set_replaces(self, store: SecretStore) -> None:
        ref = SecretRef("openproject.api_token")
        store.set(ref, "first")
        store.set(ref, "second")
        assert store.get(ref) == "second"

    def test_delete(self, store: SecretStore) -> None:
        ref = SecretRef("openproject.api_token")
        store.set(ref, "s3cret")
        store.delete(ref)
        assert store.get(ref) is None

    def test_delete_is_idempotent(self, store: SecretStore) -> None:
        store.delete(SecretRef("never.set"))

    def test_list_refs_names_secrets_without_revealing_them(self, store: SecretStore) -> None:
        store.set(SecretRef("openproject.api_token"), "s3cret")
        store.set(SecretRef("local.bearer"), "another")
        refs = store.list_refs()
        assert {ref.name for ref in refs} == {"openproject.api_token", "local.bearer"}
        assert "s3cret" not in repr(refs)

    def test_repr_never_leaks_a_secret_value(self, store: SecretStore) -> None:
        store.set(SecretRef("openproject.api_token"), "s3cret")
        assert "s3cret" not in repr(store)
        assert "s3cret" not in str(store)
