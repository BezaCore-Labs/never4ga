"""`never4ga connection list|health|set-token`.

`core/03` section 16 splits a connection in two and joins the halves by name.
These verbs are how a person works each half: `list` and `health` read the
canonical half out of the vault, and `set-token` puts the secret half where the
vault can never see it.

**All three stay in this process.** A token must not travel over a socket to be
stored, however local that socket is, and a connection's health is a question
about the machine holding the credential rather than about the index. That puts
them with `init`, `status` and the creation verbs rather than with `search`.

**Nothing here prints a token.** `set-token` reads from a prompt that does not
echo, or from a pipe when there is no terminal; it reports the *name* the value
was stored under and never the value. `details/security-configuration.md`
section 4 and `details/api-cli-mcp-contract.md` section 8.
"""

from __future__ import annotations

import getpass
import sys
from typing import Any

from never4ga.adapters.work_management import KNOWN_PROVIDERS, provider_for
from never4ga.domain.connections import Connection
from never4ga.errors import ConnectionDefinitionError, StructuredError
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.secret_store import SecretStore
from never4ga.services.connections import ConnectionRegistry, secret_ref_for

__all__ = ["connection_health", "connection_list", "connection_set_token"]


def connection_list(documents: DocumentStore, secrets: SecretStore) -> dict[str, Any]:
    """Every connection the vault defines, and whether this machine has its token."""
    registry = ConnectionRegistry(documents=documents)
    return {
        "connections": [
            {
                "connection": connection.name,
                "provider": connection.provider,
                "base_url": connection.base_url,
                "project_ref": connection.project_ref,
                # Whether, not what. A boolean is safe in a log and in an
                # agent's context; the value it stands for is not.
                "token_stored": secrets.get(secret_ref_for(connection)) is not None,
                "adapter": connection.provider.strip().casefold() in KNOWN_PROVIDERS,
            }
            for connection in registry.list_connections()
        ],
        "findings": [
            {"code": finding.code, "detail": finding.detail, "path": str(finding.path)}
            for finding in registry.findings()
        ],
    }


def connection_health(
    documents: DocumentStore, secrets: SecretStore, name: str
) -> dict[str, Any] | StructuredError:
    """Whether this connection answers, and what its instance can do.

    Capabilities are read from the instance rather than declared
    (`details/openproject-adapter.md` section 12), so this is also the verb that
    says what a *particular* server, version and token will allow.
    """
    try:
        connection = ConnectionRegistry(documents=documents).resolve(name)
    except ConnectionDefinitionError as error:
        return StructuredError(
            "connection_undefined",
            str(error),
            {"connection": name},
            repair_hint="define it as an `integration` concept in 50_System/Integrations/",
        )

    token = secrets.get(secret_ref_for(connection))
    if token is None:
        return StructuredError(
            "connection_token_missing",
            f"no token is stored on this machine for connection {name!r}",
            {"connection": name, "secret": secret_ref_for(connection).name},
            repair_hint=f"store one with `never4ga connection set-token {name}`",
        )

    provider = provider_for(connection, token=token)
    if provider is None:
        return StructuredError(
            "connection_unsupported",
            f"Never4gA has no adapter for provider {connection.provider!r}",
            {"connection": name, "provider": connection.provider},
            repair_hint="check the `provider` field on the connection's concept",
        )

    health = provider.health()
    return {
        "connection": connection.name,
        "provider": connection.provider,
        "project_ref": connection.project_ref,
        "available": health.available,
        "detail": health.detail,
        # An unreachable instance has no capabilities to report, and asking
        # would only produce a second copy of the same failure.
        "capabilities": sorted(capability.value for capability in provider.capabilities)
        if health.available
        else [],
    }


def connection_set_token(
    documents: DocumentStore, secrets: SecretStore, name: str
) -> dict[str, Any] | StructuredError:
    """Store this connection's token, reading it without echoing it."""
    try:
        connection = ConnectionRegistry(documents=documents).resolve(name)
    except ConnectionDefinitionError as error:
        return StructuredError(
            "connection_undefined",
            str(error),
            {"connection": name},
            repair_hint="define it as an `integration` concept in 50_System/Integrations/",
        )

    token = _read_token(connection)
    if not token:
        return StructuredError(
            "connection_token_empty",
            "no token was given, so nothing was stored",
            {"connection": name},
            repair_hint="pipe the token in, or type it when prompted",
        )

    reference = secret_ref_for(connection)
    secrets.set(reference, token)
    # The name, never the value. `SecretRef` has no value field precisely so
    # that this line cannot accidentally be made to print one.
    return {"connection": connection.name, "secret": reference.name, "stored": True}


def _read_token(connection: Connection) -> str:
    """From a pipe when there is one, from a prompt that does not echo when not.

    A command-line argument is deliberately not an option: it would reach the
    process table and the shell's history, which is the one place a credential
    must not be.
    """
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return getpass.getpass(f"API token for {connection.name} (not echoed): ").strip()
