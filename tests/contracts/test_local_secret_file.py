"""The protected local secrets file.

See core/05 section 21 and details/security-configuration.md sections 3-4.

It runs the same :class:`SecretStoreContract` as the in-memory fake -- a new
backend adds a subclass and a fixture, it does not restate the behaviour
(core/06 section 22) -- and then adds what is true only of a file: it lands
outside the vault, only its owner can read it, and it says out loud that it is
the fallback rather than a keyring.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import LocalSecretFileStore
from never4ga.errors import SecretStoreError
from never4ga.ports.secret_store import SecretRef, SecretStore
from tests.contracts.secret_store_contract import SecretStoreContract

pytestmark = pytest.mark.contract

REF = SecretRef("never4ga.local_api_credential")


class TestLocalSecretFileStore(SecretStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> SecretStore:
        return LocalSecretFileStore(tmp_path / "state" / "secrets.json")


class TestItProtectsWhatItWrites:
    @pytest.fixture
    def path(self, tmp_path: Path) -> Path:
        return tmp_path / "state" / "secrets.json"

    def test_nothing_is_written_until_a_secret_is_set(self, path: Path) -> None:
        # Asking where secrets live must not create a file, the same rule
        # platform_paths follows: the component that writes is the one that
        # creates.
        LocalSecretFileStore(path)
        assert not path.exists()

    def test_the_file_is_readable_only_by_its_owner(self, path: Path) -> None:
        LocalSecretFileStore(path).set(REF, "s3cret")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_the_directory_is_reachable_only_by_its_owner(self, path: Path) -> None:
        LocalSecretFileStore(path).set(REF, "s3cret")
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_a_loosened_file_is_tightened_and_reported(self, path: Path) -> None:
        store = LocalSecretFileStore(path)
        store.set(REF, "s3cret")
        path.chmod(0o644)

        reopened = LocalSecretFileStore(path)
        assert reopened.get(REF) == "s3cret"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert any("permission" in warning.casefold() for warning in reopened.warnings)

    def test_it_says_it_is_the_fallback(self, path: Path) -> None:
        # details/security-configuration.md section 4: "The fallback must
        # produce a clear warning."
        warnings = LocalSecretFileStore(path).warnings
        assert warnings
        assert any("keyring" in warning.casefold() for warning in warnings)

    def test_values_survive_a_restart(self, path: Path) -> None:
        LocalSecretFileStore(path).set(REF, "s3cret")
        assert LocalSecretFileStore(path).get(REF) == "s3cret"

    def test_deleting_the_last_secret_leaves_a_valid_file(self, path: Path) -> None:
        store = LocalSecretFileStore(path)
        store.set(REF, "s3cret")
        store.delete(REF)
        assert LocalSecretFileStore(path).list_refs() == ()

    def test_a_corrupt_file_is_refused_rather_than_silently_replaced(self, path: Path) -> None:
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        with pytest.raises(SecretStoreError):
            LocalSecretFileStore(path).get(REF)

    def test_a_corrupt_file_is_not_overwritten(self, path: Path) -> None:
        # Losing an OpenProject token because a byte was mangled is worse than
        # refusing to start.
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        with pytest.raises(SecretStoreError):
            LocalSecretFileStore(path).set(REF, "s3cret")
        assert path.read_text() == "{not json"

    def test_the_file_holds_no_vault_content(self, path: Path) -> None:
        LocalSecretFileStore(path).set(REF, "s3cret")
        document = json.loads(path.read_text())
        assert set(document) == {"version", "secrets"}


class TestItNeverRendersAValue:
    def test_repr_names_secrets_without_revealing_them(self, tmp_path: Path) -> None:
        store = LocalSecretFileStore(tmp_path / "secrets.json")
        store.set(REF, "s3cret")
        assert "s3cret" not in repr(store)
        assert REF.name in repr(store)

    def test_an_error_mentions_the_path_and_not_the_contents(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.json"
        path.write_text('{"version": 1, "secrets": {"a": "s3cret"}} trailing')
        with pytest.raises(SecretStoreError) as raised:
            LocalSecretFileStore(path).get(REF)
        assert "s3cret" not in str(raised.value)
        assert str(path) in str(raised.value)
