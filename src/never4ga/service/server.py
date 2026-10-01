"""``never4ga serve`` -- the local service (core/05 sections 10 to 13).

One per-user background process that owns the index, watches the vault and
answers on loopback. It is the last two steps of section 18's sequence -- start
the API, schedule maintenance -- wrapped around :class:`VaultRuntime`, which is
the rest of it.

Uvicorn is imported here and nowhere else. The services layer stays
synchronous: SQLite and the filesystem block, FastAPI runs ``def`` endpoints in
a threadpool, and an async services layer would buy nothing at personal scale.

The bind address comes from :class:`never4ga.config.ServiceEndpoint`, which
refuses anything that is not loopback. That check lives in the type rather than
here so that no second caller can bypass it.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from types import TracebackType
from typing import Final

import uvicorn

from never4ga.adapters.filesystem import FileSystemMarkdownStore, LocalSecretFileStore
from never4ga.adapters.git import GitSignalProvider
from never4ga.api import create_app
from never4ga.composition import (
    FileSessionStore,
    work_read_service,
    work_signal_provider,
    work_write_service,
    workspace_service,
)
from never4ga.context.inbox_signals import InboxSignalProvider
from never4ga.context.session_signals import SessionSignalProvider
from never4ga.errors import Never4gaError
from never4ga.platform_paths import PlatformPaths
from never4ga.service.runtime import ServiceSettings, StartupReport, VaultRuntime

__all__ = ["LocalService", "serve"]

_LOGGER: Final = logging.getLogger("never4ga.service")

#: How often the maintenance thread looks at the clock. Short enough that a
#: settled debounce fires promptly, long enough to cost nothing.
_PUMP_INTERVAL: Final = 0.1

#: How long ``start`` waits for uvicorn to report itself listening.
_READY_TIMEOUT: Final = 10.0


class ServiceNotRunningError(Never4gaError):
    """The service was asked for something only a running service has."""


class LocalService:
    """The daemon: a runtime, an HTTP server, and a maintenance thread.

    Usable two ways. ``run()`` blocks until the process is signalled, which is
    what ``never4ga serve`` and a systemd unit want. ``start()``/``stop()`` run
    it on a background thread, which is what a test wants. Both drive the same
    code, so the tested path is the shipped one.
    """

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        paths: PlatformPaths | None = None,
        runtime: VaultRuntime | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._runtime = runtime or VaultRuntime(settings, paths=paths, clock=clock)
        self._clock = clock
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._pump: threading.Thread | None = None
        self._stopping = threading.Event()
        self._report: StartupReport | None = None

    # -- lifecycle --------------------------------------------------------

    def run(self) -> StartupReport:
        """Start, and block until the server stops. For the foreground."""
        report = self._prepare()
        try:
            self._require_server().run()
        finally:
            self._shutdown()
        return report

    def start(self) -> StartupReport:
        """Start on a background thread and wait until it is listening."""
        report = self._prepare()
        server = self._require_server()
        self._thread = threading.Thread(target=server.run, name="never4ga-http", daemon=True)
        self._thread.start()
        self._await_ready(server)
        return report

    def stop(self) -> None:
        """Ask the server to stop, and wait for it. Idempotent."""
        self._stopping.set()
        server, self._server = self._server, None
        if server is not None:
            server.should_exit = True
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=_READY_TIMEOUT)
        self._shutdown()

    def __enter__(self) -> LocalService:
        self.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    # -- what a caller can ask -------------------------------------------

    @property
    def runtime(self) -> VaultRuntime:
        return self._runtime

    @property
    def report(self) -> StartupReport:
        if self._report is None:
            raise ServiceNotRunningError("the service has not started")
        return self._report

    @property
    def bound(self) -> tuple[str, int]:
        """The address actually listened on, which port 0 makes worth asking."""
        server = self._server
        sockets = getattr(server, "servers", None) if server is not None else None
        if not sockets:
            raise ServiceNotRunningError("the service is not listening")
        host, port = sockets[0].sockets[0].getsockname()[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.bound
        return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"

    # -- internals --------------------------------------------------------

    def _prepare(self) -> StartupReport:
        report = self._runtime.start()
        self._report = report
        for step in report.steps:
            if not step.ok:
                _LOGGER.warning("startup step %s degraded: %s", step.name, step.detail)
        for warning in report.warnings:
            _LOGGER.warning("%s", warning)

        app = create_app(
            sessions=self._runtime.sessions,
            writing_sessions=partial(self._runtime.sessions, write=True),
            # Machine-local state, this vault's own: the service resolves scope
            # the same way the CLI does, from the same file.
            workspaces=workspace_service(
                self._runtime.vault_id, FileSystemMarkdownStore(self._settings.vault)
            ),
            signal_providers=(GitSignalProvider(),),
            # The rest of `core/07` Stage F. Built per session because each
            # needs one vault's stores, and a session is per request.
            session_signal_providers=lambda session: (
                InboxSignalProvider(session.files),
                SessionSignalProvider(
                    FileSessionStore(PlatformPaths.resolve().sessions_database(session.vault_id))
                ),
                work_signal_provider(
                    documents=session.documents,
                    secrets=LocalSecretFileStore(PlatformPaths.resolve().secrets_file),
                    trackers_database=PlatformPaths.resolve().trackers_database(session.vault_id),
                ),
            ),
            # Working session state, per vault. Same shape as the tracker
            # signals above and for the same reason: it needs one vault's
            # identity, and a session is per request.
            session_store=lambda session: FileSessionStore(
                PlatformPaths.resolve().sessions_database(session.vault_id)
            ),
            # The write half of work management. `api` picks no adapters, so
            # the writer factory is assembled here, the same way the CLI does
            # it (core/03 section 16).
            work_writer=lambda session: work_write_service(
                session.documents,
                workspace_service(session.vault_id, session.documents),
                LocalSecretFileStore(PlatformPaths.resolve().secrets_file),
                PlatformPaths.resolve().trackers_database(session.vault_id),
            ),
            # And the read half, from the same composition helper the CLI uses
            # (core/05 section 15). Separate from the writer because they are
            # separately available: a read-only connection has a reader and no
            # writer.
            work_reader=lambda session: work_read_service(
                session.documents,
                workspace_service(session.vault_id, session.documents),
                LocalSecretFileStore(PlatformPaths.resolve().secrets_file),
                PlatformPaths.resolve().trackers_database(session.vault_id),
            ),
            # The credential reaches the app and stops there. Nothing logs it,
            # and no response carries it (details/security-configuration.md
            # sections 3 and 9).
            credential=self._runtime.credential,
            vault_id=self._runtime.vault_id,
        )
        endpoint = self._settings.endpoint
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=endpoint.host,
                port=endpoint.port,
                log_level="info",
                # An access log line carries a method and a path, never a
                # header. It is off because a local daemon that logs every
                # request is noise.
                access_log=False,
                lifespan="off",
            )
        )
        self._stopping.clear()
        self._pump = threading.Thread(
            target=self._maintain, name="never4ga-maintenance", daemon=True
        )
        self._pump.start()
        return report

    def _maintain(self) -> None:
        """Section 18's last step: schedule maintenance jobs.

        Reconciliation after a settled burst of filesystem events, and on a
        timer regardless. :class:`ReconcileLoop` swallows its own failures, so
        this thread outlives a bad document.
        """
        while not self._stopping.wait(_PUMP_INTERVAL):
            self._runtime.tick()

    def _shutdown(self) -> None:
        self._stopping.set()
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.join(timeout=_READY_TIMEOUT)
        self._runtime.stop()

    def _require_server(self) -> uvicorn.Server:
        if self._server is None:  # pragma: no cover - _prepare always sets it
            raise ServiceNotRunningError("the service has not been prepared")
        return self._server

    def _await_ready(self, server: uvicorn.Server) -> None:
        deadline = self._clock() + _READY_TIMEOUT
        while not server.started:
            if self._clock() > deadline:
                self.stop()
                raise ServiceNotRunningError(
                    f"the service did not start listening within {_READY_TIMEOUT:g}s"
                )
            time.sleep(0.01)


@contextmanager
def serve(
    settings: ServiceSettings, *, paths: PlatformPaths | None = None
) -> Iterator[LocalService]:
    """Run the service for the length of a ``with`` block."""
    service = LocalService(settings, paths=paths)
    service.start()
    try:
        yield service
    finally:
        with contextlib.suppress(Exception):
            service.stop()
