"""Which HTTP status a domain error deserves, and under what stable code.

`details/api-cli-mcp-contract.md` section 12 asks for a code, a message,
details, `retryable` and a repair hint. The code is the part that has to be
*stable*: an agent branches on it, and "do not force agents to parse
English-only stack traces" is the whole point of having one.

The mapping is an explicit table rather than a catch-all. A catch-all that
turned every unhandled `Never4gaError` into a 500 coded
`type(exception).__name__` would report a missing request field as a server
fault, let a class rename change the wire contract, and mark an embedder that
is merely switched off as not retryable.

**A status code is this interface's opinion, not the domain's.** `never4ga.errors`
knows nothing about HTTP and must not learn: the CLI turns the same exceptions
into exit codes without consulting any of this.

**Adapters are absent on purpose.** `core/05` section 15 forbids an interface
importing one, so this table cannot name `FrontmatterError` or
`ClientFileStoreError` even to map them -- and it should not want to. An adapter
error that escapes this far is a fault in how the service handled it rather than
something a client did: a store is supposed to *report* an unreadable document
(`doctor`, `validate`) rather than raise past the request. 500 is the honest
answer to that, and the one the fallback already gives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from never4ga.domain.sessions import SessionStateError
from never4ga.errors import (
    CapabilityNotSupportedError,
    ConfigurationError,
    ConnectionDefinitionError,
    ContextBudgetError,
    DuplicateConceptIdError,
    ExtensionOwnershipError,
    IdentityError,
    Never4gaError,
    PathNotVisibleError,
    ProviderUnavailableError,
    RepositoryMarkerError,
    ScopeResolutionError,
    SecretStoreError,
    ServiceControlError,
    SessionStoreError,
    UnknownWorkItemStatusError,
    VaultIntegrityError,
    VaultPathError,
    VectorIndexDisabledError,
    WorkItemNotFoundError,
    WorkspaceMappingStoreError,
    WriteConflictError,
    WriteNotPermittedError,
    WriteRejectedError,
)
from never4ga.services.adapters import AdapterSyncError
from never4ga.services.capture import CaptureError
from never4ga.services.creation import ConceptCreationError
from never4ga.services.inbox import InboxError
from never4ga.services.skill_authoring import SkillAuthoringError
from never4ga.services.vendoring import ProvenanceError
from never4ga.services.wrap import WorkspaceGoneError

__all__ = ["SERVER_FAULTS", "STATUS_BY_ERROR", "MappedError", "status_for"]


@dataclass(frozen=True, slots=True)
class MappedError:
    status: int
    code: str
    retryable: bool
    repair_hint: str


#: What a client can act on. Everything here says "your request, or the state
#: you asked about" rather than "this service is broken".
STATUS_BY_ERROR: Final[dict[type[Never4gaError], MappedError]] = {
    ScopeResolutionError: MappedError(
        400,
        "scope_unresolved",
        False,
        "send a scope: a workspace id, a repository root, or a working "
        "directory that is mapped to one. Scope is resolved and never guessed "
        "(core/07), because a pack built against the wrong workspace is worse "
        "than no pack",
    ),
    PathNotVisibleError: MappedError(
        400,
        "path_not_visible",
        False,
        "the service cannot see this path, so it cannot resolve a workspace from "
        "it. A sandboxed service does not share every directory its caller has -- "
        "systemd's PrivateTmp gives it a /tmp of its own -- so run the command "
        "with --local, or work from a path the service can see",
    ),
    UnknownWorkItemStatusError: MappedError(
        400,
        "unknown_work_item_status",
        False,
        "the tracker defines no status by that name; the refusal lists the ones "
        "it does define. A filter that cannot be applied is reported rather "
        "than answered with an empty result, which read as an empty backlog",
    ),
    IdentityError: MappedError(
        400,
        "invalid_identity",
        False,
        "identity is a canonical UUIDv7 (core/02 section 5.1)",
    ),
    VaultPathError: MappedError(
        400,
        "invalid_vault_path",
        False,
        "paths are vault-relative and stay inside the vault",
    ),
    ContextBudgetError: MappedError(
        400, "invalid_budget", False, "a budget needs at least one item and one character"
    ),
    ConnectionDefinitionError: MappedError(
        400,
        "invalid_connection",
        False,
        "check the connection's provider, base URL and project reference",
    ),
    WriteNotPermittedError: MappedError(
        403,
        "write_not_permitted",
        False,
        "the workspace's sync_policy or the token's scope forbids this "
        "(core/03 section 22); change the policy deliberately",
    ),
    WorkItemNotFoundError: MappedError(
        404,
        "work_item_not_found",
        False,
        "check the reference; the tracker answered, and it does not have this",
    ),
    DuplicateConceptIdError: MappedError(
        409,
        "duplicate_concept_id",
        False,
        "two documents claim one identity; give one a fresh UUIDv7. Never4gA "
        "refuses to guess which is which (core/02 section 32)",
    ),
    VaultIntegrityError: MappedError(
        409, "vault_integrity", False, "run `never4ga doctor`; canonical Markdown is the fix"
    ),
    WriteConflictError: MappedError(
        409,
        "write_conflict",
        # Deliberately not retryable. Refetching the version and sending again
        # is exactly the silent overwrite this check exists to prevent, so a
        # person decides what to do rather than a retry loop.
        False,
        "the item changed since it was read; look at it again before writing",
    ),
    VectorIndexDisabledError: MappedError(
        409,
        "vector_index_disabled",
        False,
        "this vault has no vector index; Never4gA ships no implementation of one",
    ),
    CapabilityNotSupportedError: MappedError(
        409,
        "capability_not_supported",
        False,
        "this backend does not offer that capability; check `capabilities`",
    ),
    ExtensionOwnershipError: MappedError(
        409,
        "extension_not_owned",
        False,
        "Never4gA does not overwrite what it does not manage (core/09 section 11); adopt "
        "it deliberately or leave it alone",
    ),
    WriteRejectedError: MappedError(
        422, "write_rejected", False, "the tracker refused the values; its message says why"
    ),
    CaptureError: MappedError(
        400, "nothing_to_capture", False, "give some text, or a path that exists"
    ),
    # 409 rather than 400: the request is legible and the item is real; what
    # is wrong is the *state* -- nothing yet accounts for the thing being
    # cleared. Not retryable, because repeating it changes nothing: something
    # has to be filed first, and that is a different call.
    InboxError: MappedError(
        409,
        "not_accounted_for",
        False,
        "file the item first, then clear it; nothing leaves the Inbox until "
        "something accounts for it",
    ),
    SessionStateError: MappedError(
        409,
        "session_state",
        False,
        "the session has already been wrapped, or was never opened; open a new one",
    ),
    WorkspaceGoneError: MappedError(
        409,
        "workspace_gone",
        False,
        "the session's workspace is no longer in the vault; say where the log "
        "belongs with `into`: the workspace it became, or the life area that holds it",
    ),
    AdapterSyncError: MappedError(
        409,
        "adapter_sync",
        False,
        "the plan no longer matches what is on disk; re-plan and look at it before applying",
    ),
    # 422 rather than 404: the subject path is well-formed and the request is
    # legible, but there is nothing there to hash. Not retryable -- a directory
    # does not gain files by asking again.
    ProvenanceError: MappedError(
        422,
        "provenance_unrecordable",
        False,
        "the subject directory holds no files, so there is nothing to record; "
        "vendor the material first, then record where it came from",
    ),
    SkillAuthoringError: MappedError(
        422,
        "cannot_author_skill",
        False,
        "the Skill could not be started: the name is outside the Agent Skills format, "
        "is one Never4gA ships, or is already there",
    ),
    ConceptCreationError: MappedError(
        422,
        "concept_rejected",
        False,
        "the concept did not validate, or its location is not where its type belongs "
        "(core/01); the report says which",
    ),
    ProviderUnavailableError: MappedError(
        503,
        "provider_unavailable",
        # The one that has to be true. An unreachable provider is a normal
        # state, and a client told `retryable: false` about a machine that is
        # merely switched off will give up on work it should keep.
        True,
        "the external system did not answer; nothing was lost and the work is "
        "picked up when it does",
    ),
}

#: Decided to be *this service's* problem. Listed rather than defaulted, so the
#: 500 is a judgement and a test can tell the difference between an error
#: somebody considered and an error nobody did.
SERVER_FAULTS: Final[frozenset[type[Never4gaError]]] = frozenset(
    {
        ConfigurationError,
        SecretStoreError,
        # This machine's session database is unusable: locked, full,
        # unmigratable or corrupt. As with the secrets and the workspace
        # mappings, nothing the client sent is wrong, and no request it could
        # send instead would work.
        #
        # A `/v1/work` write is the exception and does not reach this table:
        # by the time it records, the tracker has already changed, so
        # `record_tracker_write` catches this and the write answers 200 with
        # `record_lost` set. A 500 there would make a client retry a create
        # it had already made.
        SessionStoreError,
        ServiceControlError,
        RepositoryMarkerError,
        WorkspaceMappingStoreError,
    }
)

_FAULT: Final = MappedError(500, "internal_error", False, "run `never4ga doctor`")


def status_for(exception: Never4gaError) -> MappedError:
    """The most specific mapping for this exception's class.

    Walked along the MRO so a subclass beats its parent without the table
    needing to repeat itself -- `DuplicateConceptIdError` is a
    `VaultIntegrityError` and gets its own code, and adding a subclass of
    something already mapped inherits a sensible answer rather than a 500.
    """
    for cls in type(exception).__mro__:
        mapped = STATUS_BY_ERROR.get(cls)
        if mapped is not None:
            return mapped
    return _FAULT
