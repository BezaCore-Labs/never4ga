"""The connection registry -- resolving a named connection from the vault.

core/03 section 16 splits connection configuration by secrecy: the portable
half lives in canonical Markdown, the secret half never does. This service owns
the portable half. It reads `integration` concepts (core/02 section 21.19,
located in ``50_System/Integrations/``), turns each into a
:class:`~never4ga.domain.connections.Connection`, and refuses any that carries
a secret.

**It reads the canonical store, not the index.** Every other lookup in the
context path goes through :class:`MetadataIndex` because it is a *retrieval*
question and speed matters. This is a configuration question: a connection that
failed to resolve because somebody had not re-indexed would be wrong in a way
that is hard to notice, and core/05 section 19 wants the system useful with
Never4gA not running. `doctor` and `validate` read the store directly for the
same reason.

**Refusal reports rather than repairs.** A definition carrying an ``api_token``
is not silently stripped and rewritten -- core/02 makes validation always
report. :meth:`ConnectionRegistry.findings` is what `doctor` surfaces, and the
document is left exactly as the user wrote it.

Nothing here holds a token. :func:`secret_ref_for` names one; only the
machine-local :class:`SecretStore` can turn that name into a value.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from never4ga.domain.connections import (
    FORBIDDEN_CONNECTION_KEYS,
    Connection,
    secret_name,
)
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.errors import ConnectionDefinitionError
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.secret_store import SecretRef

__all__ = [
    "ConnectionFinding",
    "ConnectionRegistry",
    "project_ref_for",
    "secret_ref_for",
]

#: The registered type that carries a connection definition (core/02 21.19).
INTEGRATION_TYPE: Final = "integration"

#: The frontmatter key that makes an `integration` concept a *connection*
#: rather than a page describing one. details/openproject-adapter.md section 4
#: uses the same three key names on a workspace, so a reader meets one
#: vocabulary rather than two.
CONNECTION_KEY: Final = "connection"

#: Where core/02 section 21.19 locates the type. Named here for error messages;
#: `never4ga.schema` remains the authority on the location itself.
INTEGRATIONS_DIRECTORY: Final = "50_System/Integrations"


@dataclass(frozen=True, slots=True)
class ConnectionFinding:
    """Something wrong with a connection definition, reported not repaired.

    Deliberately not `never4ga.services.doctor.Finding`: `doctor` reads this
    module, so this module must not read `doctor`. The mapping happens there.
    """

    code: str
    detail: str
    path: VaultPath | None = None


def secret_ref_for(connection: Connection) -> SecretRef:
    """The store reference holding this connection's token.

    A name, never a value. ``SecretRef`` has no value field precisely so that a
    reference cannot be serialised with its secret attached.
    """
    return SecretRef(secret_name(connection))


class ConnectionRegistry:
    """Every connection the vault defines."""

    def __init__(self, documents: DocumentStore) -> None:
        self._documents = documents

    def resolve(self, name: str) -> Connection:
        """The connection called ``name``.

        Raises :class:`ConnectionDefinitionError` when there is none, rather
        than returning ``None``: a missing connection is a configuration fault
        the caller has to see, not an empty result it might quietly skip.
        """
        for document in self._connection_documents():
            if str(document.frontmatter.get(CONNECTION_KEY, "")).strip() == name:
                self._reject_secrets(document)
                return _to_connection(document)
        raise ConnectionDefinitionError(
            f"no connection named {name!r} is defined in {INTEGRATIONS_DIRECTORY}"
        )

    def list_connections(self) -> tuple[Connection, ...]:
        """Every well-formed connection, skipping any that carries a secret.

        A poisoned definition is not silently usable, but neither does it stop
        the others from resolving -- :meth:`findings` is where it surfaces.
        """
        found = []
        for document in self._connection_documents():
            try:
                self._reject_secrets(document)
                found.append(_to_connection(document))
            except ConnectionDefinitionError:
                continue
        return tuple(found)

    def findings(
        self, among: Sequence[StoredDocument] | None = None
    ) -> tuple[ConnectionFinding, ...]:
        """What `doctor` should say about the connection definitions.

        ``among`` is the corpus a caller has already loaded. `doctor` holds one
        by the time it gets here, and walking the vault again to find the
        handful of `integration` documents in it would be a large share of a
        diagnosis. Omitting it walks the store, which is what every other
        caller wants.
        """
        findings = []
        for document in self._connection_documents(among):
            leaked = sorted(FORBIDDEN_CONNECTION_KEYS & set(document.frontmatter))
            if leaked:
                findings.append(
                    ConnectionFinding(
                        code="connection.secret_in_vault",
                        detail=(
                            f"connection definition carries {', '.join(leaked)}; "
                            "core/03 section 16 keeps secrets out of the vault. "
                            "Remove the key and store the value in the secret store, "
                            f"named {secret_name(_to_connection(document))!r}."
                        ),
                        path=document.path,
                    )
                )
        return tuple(findings)

    def _connection_documents(
        self, among: Sequence[StoredDocument] | None = None
    ) -> Iterator[StoredDocument]:
        for document in self._documents.iter_documents() if among is None else among:
            frontmatter = document.frontmatter
            if frontmatter.get("type") != INTEGRATION_TYPE:
                continue
            # An `integration` concept documenting an integration is not itself
            # a connection. The key is what distinguishes them.
            if not str(frontmatter.get(CONNECTION_KEY, "")).strip():
                continue
            yield document

    @staticmethod
    def _reject_secrets(document: StoredDocument) -> None:
        leaked = sorted(FORBIDDEN_CONNECTION_KEYS & set(document.frontmatter))
        if leaked:
            # Name the keys, never the values: refusing a leaked secret by
            # printing it into an exception message is not refusing it.
            raise ConnectionDefinitionError(
                f"connection definition at {document.path} carries "
                f"{', '.join(leaked)}; core/03 section 16 forbids secrets in the vault"
            )


def _optional(frontmatter: Any, key: str) -> str | None:
    value = str(frontmatter.get(key, "")).strip()
    return value or None


def _to_connection(document: StoredDocument) -> Connection:
    frontmatter = document.frontmatter
    return Connection(
        name=str(frontmatter.get(CONNECTION_KEY, "")).strip(),
        provider=str(frontmatter.get("provider", "")).strip(),
        base_url=_optional(frontmatter, "base_url"),
        project_ref=_optional(frontmatter, "project_ref"),
    )


def project_ref_for(declaration: Mapping[str, Any] | None, connection: Connection) -> str:
    """Which project a workspace's work belongs to.

    `core/03` section 15 puts the declaration on the *workspace*; the connection
    is the machine's half -- where the server is and which token opens it. One
    connection serves every workspace on a machine, so the workspace is the
    authority and the connection is only the fallback for a workspace that
    names none.

    **One function, so the read path and the signal provider cannot disagree.**
    Taking the project from the connection would make every workspace's
    startup pack report the connection's default project and its work items,
    and the fault would be invisible from the one workspace that happens to be
    that default.
    """
    declared = ""
    if isinstance(declaration, Mapping):
        declared = str(declaration.get("project_ref", "")).strip()
    return declared or (connection.project_ref or "")
