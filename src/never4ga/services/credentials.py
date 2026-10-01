"""The local API credential (details/security-configuration.md sections 3, 8, 9).

Binding loopback keeps the service off the network; it does not keep other
processes on the machine out. core/05 section 12 therefore asks for a local
bearer credential even there, and details/api-cli-mcp-contract.md section 8
adds that it is stored outside the vault and never returned to model content.

The credential is generated on first use and then reused, because a value that
changed on every start would invalidate every configured client each time. It
is reached through the :class:`SecretStore` port, so replacing the protected
local file with an OS keyring later is an adapter change and nothing else.

Nothing in this module logs, prints or returns the credential except to the
caller that asked for it.
"""

from __future__ import annotations

import secrets

from never4ga.ports.secret_store import SecretRef, SecretStore

__all__ = ["LOCAL_API_CREDENTIAL", "credential_matches", "ensure_local_credential"]

#: The name under which the local bearer credential is stored. A name is safe
#: to log; the value it points at is not.
LOCAL_API_CREDENTIAL = SecretRef("never4ga.local_api_credential")

#: 32 bytes of entropy, rendered URL-safe so it survives an Authorization
#: header and a TOML string without quoting.
_ENTROPY_BYTES = 32


def ensure_local_credential(store: SecretStore, *, rotate: bool = False) -> str:
    """Return the local bearer credential, generating one if there is none.

    ``rotate`` discards the existing credential and issues a new one. Every
    configured client has to be told the new value, which is why it is never
    the default.
    """
    if not rotate:
        existing = store.get(LOCAL_API_CREDENTIAL)
        if existing:
            return existing
    credential = secrets.token_urlsafe(_ENTROPY_BYTES)
    store.set(LOCAL_API_CREDENTIAL, credential)
    return credential


def credential_matches(expected: str, presented: str | None) -> bool:
    """Compare in constant time, and fail closed.

    An empty ``expected`` means the service has no credential to check against.
    That is a misconfiguration, and the safe reading of it is that nothing
    authenticates -- never that everything does.
    """
    if not expected or not presented:
        return False
    return secrets.compare_digest(expected, presented)
