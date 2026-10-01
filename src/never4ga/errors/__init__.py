"""Never4gA error hierarchy.

Every error raised by Never4gA core derives from :class:`Never4gaError` so that
callers can distinguish system errors from backend/provider exceptions leaking
through an adapter. Adapters are expected to translate provider-native
exceptions into these types at their boundary.
"""

from __future__ import annotations

from never4ga.errors.reporting import StructuredError

__all__ = [
    "CapabilityNotSupportedError",
    "ConfigurationError",
    "ConnectionDefinitionError",
    "ContextBudgetError",
    "DuplicateConceptIdError",
    "ExtensionOwnershipError",
    "IdentityError",
    "Never4gaError",
    "PathNotVisibleError",
    "ProviderUnavailableError",
    "RepositoryMarkerError",
    "ScopeResolutionError",
    "SecretStoreError",
    "ServiceControlError",
    "SessionStoreError",
    "StructuredError",
    "VaultIntegrityError",
    "VaultPathError",
    "VectorIndexDisabledError",
    "WorkItemNotFoundError",
    "WorkspaceMappingStoreError",
    "WriteConflictError",
    "WriteNotPermittedError",
    "WriteRejectedError",
]


class Never4gaError(Exception):
    """Base class for all Never4gA errors."""


class IdentityError(Never4gaError, ValueError):
    """A value was not a valid Never4gA identity.

    Raised when a caller supplies something that is not a canonical UUIDv7 --
    including backend-native identities such as SQLite rowids, PostgreSQL
    serials or provider memory IDs (core/06 section 3, core/08 section 15).
    """


class VaultPathError(Never4gaError, ValueError):
    """A vault-relative path was malformed or escaped the vault root."""


class VaultIntegrityError(Never4gaError):
    """The canonical vault contradicts itself.

    Reported rather than repaired: core/02 section 23 requires repair to be an
    explicit act, and canonical data is never discarded merely because
    maintenance found a problem (core/02 section 32).
    """


class DuplicateConceptIdError(VaultIntegrityError):
    """Two documents claim the same canonical identity.

    core/02 section 5.1 requires ``id`` to be unique within a vault. Rather than
    pick a winner, Never4gA refuses to answer for that identity and leaves both
    documents readable by path.
    """


class ScopeResolutionError(Never4gaError):
    """Mechanical scope resolution could not produce a workspace.

    Per core/07 section 3 Stage A, scope resolution is deterministic. Failing
    loudly is correct; guessing a workspace is not.
    """


class PathNotVisibleError(ScopeResolutionError):
    """The path a scope names is not there, as the resolving process sees it.

    A service sandboxed with systemd's `PrivateTmp=true` has a `/tmp` of its
    own, so a worktree under its caller's `/tmp` does not exist for it.
    Reporting that as an unmapped repository would send the reader to map
    something already mapped. A subclass, so a caller that only cares that no
    scope resolved still catches it.
    """


class ServiceControlError(Never4gaError):
    """The background service could not be started, stopped or restarted.

    Reporting always answers (core/05 section 20); *control* fails loudly, so a
    caller never believes a service is running because a command returned
    quietly.
    """


class SecretStoreError(Never4gaError):
    """A secret store could not be read or written.

    Raised rather than treating a damaged store as empty: a store that read a
    corrupt file as "no secrets" would overwrite it on the next write, and the
    credential it replaced would be gone
    (details/security-configuration.md section 4).
    """


class SessionStoreError(Never4gaError):
    """The machine-local session store could not be read or written.

    Distinct from :class:`never4ga.domain.sessions.SessionStateError`, which
    says the *caller* asked for something incoherent -- a session never opened,
    an id opened twice. This one says the store itself is the problem: the file
    is locked past its busy timeout, the disk is full, a migration failed, or
    ``sessions.sqlite3`` is corrupt.

    Declared so that a caller can decide what a lost record costs it, which is
    not the same everywhere. `wrap` reads this store to hold a session to
    account and must fail loudly if it cannot. ``record_tracker_write`` runs
    *after* a tracker has already changed, and must not report somebody's
    ticket as unchanged because bookkeeping failed -- so it catches this
    and says the record was lost instead.

    An adapter translates at its boundary, as :class:`SecretStoreError` and
    :class:`WorkspaceMappingStoreError` are translated for the other two
    machine-local durable stores: `services` may not import `adapters`, so
    without a declared error the only thing a service could catch is
    ``sqlite3``, which it is not allowed to know about.
    """


class CapabilityNotSupportedError(Never4gaError):
    """A backend or provider was asked for a capability it does not declare.

    core/06 section 21: core behaviour checks capabilities instead of
    hard-coding vendor names.
    """


class VectorIndexDisabledError(CapabilityNotSupportedError):
    """A write was attempted against a disabled VectorIndex.

    Reads degrade to an empty result set (core/05 section 19); writes fail
    loudly so that callers do not believe vectors were persisted.
    """


class ConfigurationError(Never4gaError):
    """Machine-local configuration is malformed or asks for something refused.

    core/05 section 9 keeps machine-local configuration outside the vault, and
    section 12 restricts the service to loopback. A configuration that would
    breach either is refused at the point it is read, rather than partly
    honoured.
    """


class ContextBudgetError(Never4gaError):
    """A context budget was configured incoherently."""


class RepositoryMarkerError(Never4gaError):
    """A repository marker exists and does not name a usable workspace.

    Refused rather than ignored. A marker that is present but unreadable is a
    mistake worth surfacing: treating it as absent would silently fall back to
    the registry and hide the fact that somebody wrote something wrong.
    """


class WorkspaceMappingStoreError(Never4gaError):
    """The machine-local repo-to-workspace mappings cannot be read or written.

    These mappings are durable rather than derived (core/05 section 9): the
    vault does not know where a repository sits on this machine, so nothing can
    rebuild them. A damaged file is therefore refused rather than read as "no
    mappings", which would destroy it on the next save.
    """


class ExtensionOwnershipError(Never4gaError):
    """An extension operation would violate ownership safety.

    core/09 section 11: Never4gA must preserve unmanaged capabilities, refuse
    silent overwrite, and remove only entries proven to be Never4gA-managed.
    """


class ConnectionDefinitionError(Never4gaError, ValueError):
    """A connection definition was malformed, or carried a secret.

    core/03 section 16 splits connection configuration in two and forbids the
    wrong half in the vault: a name may live in canonical Markdown, a token
    never may. Raised for both -- a definition missing what it needs, and one
    carrying what it must not.
    """


class WorkItemNotFoundError(Never4gaError):
    """A named external work item is not there.

    Distinct from :class:`ProviderUnavailableError`, because the tracker
    answered: the reference is wrong, or the item was deleted or moved. A
    Context Pack degrades on it rather than failing.
    """


class WriteConflictError(Never4gaError):
    """A tracker write was refused because its version had been overtaken.

    core/03 section 22: "Never4gA must not overwrite newer tracker state using
    stale cached data." details/openproject-adapter.md section 6 makes it the
    adapter's job to "return conflict errors rather than overwrite newer
    changes", and OpenProject enforces it with a 409 on a stale ``lockVersion``.

    Nothing retries on this. Refetching the version and sending again is
    precisely the silent overwrite the constraint forbids, so the decision to
    try again belongs to whoever can see what the other change was.
    """


class WriteRejectedError(Never4gaError):
    """A tracker write did not make the change it was asked to make.

    Raised both when the provider says so and when its own answer shows it.
    OpenProject ignores an unknown field rather than rejecting it -- 200, no
    error, no version bump -- so "the provider refused this" and "the provider
    accepted this and did nothing" are the same failure wearing different
    faces, and a caller's response to either is the same: do not retry, read
    the message.

    Distinct from :class:`WriteConflictError`, which means somebody else got
    there first, and from :class:`WriteNotPermittedError`, which means Never4gA
    may not ask.
    """


class WriteNotPermittedError(Never4gaError):
    """A tracker write was asked for that policy does not permit.

    core/03 section 22: "write only when explicitly requested/authorized". The
    refusal is a distinct type rather than a returned false, because a caller
    that forgets to check a boolean writes anyway, and the thing being written
    to is somebody else's system of record.

    Distinct from :class:`CapabilityNotSupportedError`, which says the provider
    cannot do it. This says Never4gA may not ask.
    """


class UnknownWorkItemStatusError(Never4gaError):
    """A status filter naming something the tracker does not define.

    Distinct from an unreachable tracker and from an empty result, because it
    is neither: the question could not be asked. Returning nothing instead
    makes a typo read as an empty backlog, which is a sentence nobody said
    (`core/05` section 19).
    """

    def __init__(self, unknown: tuple[str, ...], known: tuple[str, ...]) -> None:
        self.unknown = unknown
        self.known = known
        named = ", ".join(repr(name) for name in unknown)
        super().__init__(f"the tracker defines no status {named}")


class ProviderUnavailableError(Never4gaError):
    """An optional external provider is unavailable.

    Optional-subsystem failure must degrade rather than destroy the service
    (core/05 section 19, core/08 section 14).
    """
