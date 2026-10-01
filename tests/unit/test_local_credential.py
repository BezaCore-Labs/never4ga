"""The local API credential (details/security-configuration.md sections 3 and 9).

Loopback is not authentication. Every process on the machine can reach
127.0.0.1, so a bearer credential protects the API even there
(core/05 section 12).

The tests that matter here are the ones about *leaking*. A credential that
reaches a log line, a traceback or an agent's context has already failed, and
that failure is silent: nothing breaks, so nothing tells you.
"""

from __future__ import annotations

import logging

import pytest

from never4ga.adapters.fakes import InMemorySecretStore
from never4ga.ports.secret_store import SecretStore
from never4ga.services.credentials import (
    LOCAL_API_CREDENTIAL,
    credential_matches,
    ensure_local_credential,
)


@pytest.fixture
def store() -> SecretStore:
    return InMemorySecretStore()


class TestGeneration:
    def test_generates_a_credential_on_first_use(self, store: SecretStore) -> None:
        assert store.get(LOCAL_API_CREDENTIAL) is None
        credential = ensure_local_credential(store)
        assert credential
        assert store.get(LOCAL_API_CREDENTIAL) == credential

    def test_reuses_the_stored_credential(self, store: SecretStore) -> None:
        # `serve` runs more than once. A credential that changed every start
        # would invalidate every configured client each time.
        first = ensure_local_credential(store)
        assert ensure_local_credential(store) == first

    def test_two_vaults_do_not_share_a_credential(self) -> None:
        assert ensure_local_credential(InMemorySecretStore()) != ensure_local_credential(
            InMemorySecretStore()
        )

    def test_the_credential_is_long_and_url_safe(self, store: SecretStore) -> None:
        # It travels in an Authorization header and in a config file, so it
        # must survive both without quoting.
        credential = ensure_local_credential(store)
        assert len(credential) >= 32
        assert credential.isascii()
        assert all(character.isalnum() or character in "-_" for character in credential)

    def test_rotation_replaces_the_credential(self, store: SecretStore) -> None:
        first = ensure_local_credential(store)
        second = ensure_local_credential(store, rotate=True)
        assert second != first
        assert store.get(LOCAL_API_CREDENTIAL) == second


class TestComparison:
    def test_matches_the_credential(self) -> None:
        assert credential_matches("expected", "expected")

    def test_rejects_a_different_credential(self) -> None:
        assert not credential_matches("expected", "other")

    def test_rejects_a_missing_credential(self) -> None:
        assert not credential_matches("expected", None)

    def test_rejects_the_empty_string(self) -> None:
        assert not credential_matches("expected", "")

    def test_a_service_with_no_credential_accepts_nothing(self) -> None:
        # Fail closed. An empty expected value must never mean "anything goes".
        assert not credential_matches("", "")
        assert not credential_matches("", "anything")


class TestItNeverLeaks:
    def test_the_reference_carries_no_value(self) -> None:
        # SecretRef is a name. It is what may appear in a log or a config file,
        # which is only true while it cannot carry the secret with it.
        assert not hasattr(LOCAL_API_CREDENTIAL, "value")
        assert str(LOCAL_API_CREDENTIAL) == LOCAL_API_CREDENTIAL.name

    def test_generating_one_logs_nothing_containing_it(
        self, store: SecretStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            credential = ensure_local_credential(store)
        assert credential not in caplog.text

    def test_the_store_does_not_render_it(self, store: SecretStore) -> None:
        credential = ensure_local_credential(store)
        assert credential not in repr(store)
        assert credential not in str(store)
