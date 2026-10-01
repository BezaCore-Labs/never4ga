"""Which adapter a connection's ``provider`` field means.

`core/03` section 15 makes ``provider`` an adapter identifier -- ``openproject``,
``github``, ``jira``, ``linear`` -- and something has to turn that string into a
class. This module is that something, and it is the *only* place outside an
adapter package that may know a provider by name
(`details/openproject-adapter.md` section 1, enforced in
`tests/architecture/test_layering.py`).

Keeping it here rather than in the composition roots means the CLI and the
daemon agree about what ``openproject`` means without either of them saying it,
and `details/openproject-adapter.md` section 14's second adapter is one entry
added here rather than an edit in two roots.

It builds the raw provider only. Caching is `never4ga.services.trackers`, and
services sit beside adapters rather than beneath them, so the composition root
wraps what this returns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from never4ga.adapters.openproject import OpenProjectProvider, OpenProjectWriter
from never4ga.domain.connections import Connection
from never4ga.errors import ProviderUnavailableError
from never4ga.ports.work_management import WorkManagementProvider, WorkManagementWriter

__all__ = [
    "KNOWN_PROVIDERS",
    "KNOWN_WRITERS",
    "ProviderBuilder",
    "WriterBuilder",
    "provider_for",
    "writer_for",
]

type ProviderBuilder = Callable[[Connection, str], WorkManagementProvider]


def _openproject(connection: Connection, token: str) -> WorkManagementProvider:
    return OpenProjectProvider.from_connection(connection, token=token)


#: Adapter id to builder. The key is the adapter's own `provider_id`, so the
#: string lives in one place -- the adapter that answers to it.
KNOWN_PROVIDERS: Final[Mapping[str, ProviderBuilder]] = {
    OpenProjectProvider.provider_id: _openproject,
}


def provider_for(connection: Connection, *, token: str | None) -> WorkManagementProvider | None:
    """The adapter for this connection, or ``None`` if there is nothing to build.

    ``None`` rather than an exception for the two ordinary cases: a provider
    Never4gA has no adapter for, and a connection whose token is not in this
    machine's secret store. Neither is a fault in the vault, and neither should
    stop a Context Pack assembling -- `core/05` section 19 again.

    A connection that is defined but unusable -- no base URL, say -- still
    raises, because that *is* a fault, and one `doctor` should be able to see.
    """
    builder = KNOWN_PROVIDERS.get(connection.provider.strip().casefold())
    if builder is None or not token:
        return None
    try:
        return builder(connection, token)
    except ProviderUnavailableError:
        return None


type WriterBuilder = Callable[[Connection, str], WorkManagementWriter]


def _openproject_writer(connection: Connection, token: str) -> WorkManagementWriter:
    return OpenProjectWriter.from_connection(connection, token=token)


#: The same mapping for adapters that can also write. Separate from
#: :data:`KNOWN_PROVIDERS` rather than a flag on it, because a provider that
#: cannot write is a legitimate entry there and must not be reachable from
#: here: asking for a writer and getting a reader is exactly the confusion the
#: two Protocols exist to prevent.
KNOWN_WRITERS: Final[Mapping[str, WriterBuilder]] = {
    OpenProjectWriter.provider_id: _openproject_writer,
}


def writer_for(connection: Connection, *, token: str | None) -> WorkManagementWriter | None:
    """The writable adapter for this connection, or ``None``.

    ``None`` for the same two ordinary cases :func:`provider_for` has, plus a
    third: a provider Never4gA can read and not write. A caller that wanted to
    write says so; getting nothing back is the honest answer.
    """
    builder = KNOWN_WRITERS.get(connection.provider.strip().casefold())
    if builder is None or not token:
        return None
    try:
        return builder(connection, token)
    except ProviderUnavailableError:
        return None
