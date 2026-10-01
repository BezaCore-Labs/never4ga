"""Connection definitions -- the canonical half of a link to an external system.

core/03 section 16 splits connection configuration in two, and the split is by
*secrecy* rather than by convenience:

- **portable/canonical** -- connection name, provider type, base URL if it is
  safe to share, adapter policy. This lives in the vault, in an ``integration``
  concept under ``50_System/Integrations/``.
- **local secret** -- the API token, client secret, OAuth refresh token or
  webhook secret. These live in the :class:`SecretStore` and never enter the
  vault at all.

The two halves are joined by *name*: a connection names its secret, and only
the machine-local store can turn that name into a value.

This module is domain: it validates a definition and says what its secret is
*called*, as a plain string. Turning that name into a ``SecretRef`` belongs to
`never4ga.services.connections`, because ``SecretRef`` is a port type and
domain sits below ports. The separation is not bureaucracy -- it is what keeps
the layer that defines a connection unable to reach the store that holds one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from never4ga.errors import ConnectionDefinitionError

__all__ = [
    "FORBIDDEN_CONNECTION_KEYS",
    "SECRET_PREFIX",
    "Connection",
    "secret_name",
]

#: Frontmatter keys that may never appear on a connection definition. core/03
#: section 16 names all four under "local secret/runtime configuration". This
#: is a floor rather than a complete list of every way a secret could be
#: spelled -- see `never4ga.services.connections` for the check that treats an
#: unrecognised secret-shaped key as suspicious too.
FORBIDDEN_CONNECTION_KEYS: Final = frozenset(
    {
        "api_token",
        "password",
        "oauth_refresh_token",
        "webhook_secret",
    }
)

#: Namespace for connection secrets in the secret store. The prefix keeps them
#: distinguishable from the local API credential, which lives beside them.
SECRET_PREFIX: Final = "never4ga.connection."


@dataclass(frozen=True, slots=True)
class Connection:
    """A named link to an external system, carrying nothing secret.

    ``base_url`` is optional because core/03 section 16 makes it optional --
    "if safe to share". ``project_ref`` is optional because a connection may be
    defined before a project is chosen; it is the middle term of core/03
    section 17's ``connection + project_ref + external_id`` identity.
    """

    name: str
    provider: str
    base_url: str | None = None
    project_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConnectionDefinitionError("a connection requires a non-empty name")
        if not self.provider.strip():
            raise ConnectionDefinitionError(
                f"connection {self.name!r} requires a non-empty provider type"
            )


def secret_name(connection: Connection) -> str:
    """The name under which this connection's token is stored.

    A name, not a value: this string is safe in canonical Markdown and in logs,
    which is the whole point of the split.
    """
    return f"{SECRET_PREFIX}{connection.name}"
