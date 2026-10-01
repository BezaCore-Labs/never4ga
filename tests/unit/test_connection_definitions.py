"""Connection definitions -- the canonical half of a provider connection.

Specification:
- core/03 section 16 -- connection config is split: portable/canonical in the
  vault, secrets never. The forbidden keys are named there.
- core/03 section 17 -- an external work item is connection + project_ref +
  external_id.
- details/openproject-adapter.md section 4 -- the workspace carries only
  connection, provider and project_ref.
"""

from __future__ import annotations

import pytest

from never4ga.domain.connections import (
    FORBIDDEN_CONNECTION_KEYS,
    Connection,
    secret_name,
)
from never4ga.errors import ConnectionDefinitionError


class TestConnection:
    def test_carries_the_canonical_half(self) -> None:
        c = Connection(
            name="work_openproject",
            provider="openproject",
            base_url="https://pm.example.dev",
            project_ref="never4ga",
        )
        assert c.name == "work_openproject"
        assert c.provider == "openproject"
        assert c.base_url == "https://pm.example.dev"
        assert c.project_ref == "never4ga"

    def test_base_url_and_project_ref_are_optional(self) -> None:
        # core/03 section 16 makes the base URL optional ("if safe to share"),
        # and a connection may exist before a project is chosen.
        c = Connection(name="tracker", provider="openproject")
        assert c.base_url is None
        assert c.project_ref is None

    @pytest.mark.parametrize("field", ["name", "provider"])
    def test_name_and_provider_are_required(self, field: str) -> None:
        kwargs = {"name": "tracker", "provider": "openproject", field: "  "}
        with pytest.raises(ConnectionDefinitionError):
            Connection(**kwargs)

    def test_is_frozen(self) -> None:
        c = Connection(name="tracker", provider="openproject")
        with pytest.raises(AttributeError):
            c.name = "other"  # type: ignore[misc]


class TestSecretNaming:
    def test_the_secret_is_keyed_by_connection_name(self) -> None:
        # The vault names the secret; only the machine-local store holds it.
        name = secret_name(Connection(name="work_openproject", provider="openproject"))
        assert "work_openproject" in name

    def test_two_connections_do_not_share_a_secret(self) -> None:
        a = secret_name(Connection(name="home", provider="openproject"))
        b = secret_name(Connection(name="work", provider="openproject"))
        assert a != b

    def test_it_is_a_plain_string_not_a_port_type(self) -> None:
        # domain sits below ports, so it names the secret without reaching for
        # SecretRef. `never4ga.services.connections` does the wrapping.
        assert isinstance(secret_name(Connection(name="home", provider="openproject")), str)


class TestForbiddenKeys:
    def test_the_core_03_secrets_are_forbidden(self) -> None:
        for key in ("api_token", "password", "oauth_refresh_token"):
            assert key in FORBIDDEN_CONNECTION_KEYS

    def test_a_webhook_secret_is_forbidden_too(self) -> None:
        # core/03 section 16 lists it under local secret configuration.
        assert "webhook_secret" in FORBIDDEN_CONNECTION_KEYS
