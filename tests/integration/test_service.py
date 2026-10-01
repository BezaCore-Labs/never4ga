"""The running service.

These start a real Uvicorn on a real loopback socket and talk to it with
httpx, because the questions here (does it bind loopback only, does a stop
actually stop, do two reads overlap) are about the process rather than the
app.

They are the slowest tests in the suite by some distance, and deliberately few.
Everything that can be decided without a socket is decided in
``test_service_runtime.py`` and ``test_api.py``.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.config import ServiceEndpoint
from never4ga.errors import ConfigurationError
from never4ga.platform_paths import PlatformPaths
from never4ga.service import LocalService, ServiceSettings
from never4ga.services import ContentService, VaultInitializer

#: Generous: an inotify event and a debounce have to happen inside it, and a
#: loaded CI runner is slower than a laptop.
TIMEOUT = 15.0


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents).initialize("Served Vault")
    ContentService(files, documents).create_knowledge("Hybrid Retrieval")
    return root


@pytest.fixture
def paths(tmp_path: Path) -> PlatformPaths:
    return PlatformPaths.resolve(
        environment={
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        }
    )


@pytest.fixture
def settings(vault: Path) -> ServiceSettings:
    return ServiceSettings(
        vault=vault,
        endpoint=ServiceEndpoint(port=0),
        quiet_period=0.2,
        reconcile_interval=3600.0,
    )


@pytest.fixture
def service(settings: ServiceSettings, paths: PlatformPaths) -> Iterator[LocalService]:
    running = LocalService(settings, paths=paths)
    running.start()
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture
def client(service: LocalService) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=service.base_url,
        headers={"Authorization": f"Bearer {service.runtime.credential}"},
        timeout=TIMEOUT,
    ) as connected:
        yield connected


def _until(predicate: object, *, timeout: float = TIMEOUT) -> bool:
    """Poll rather than sleep a fixed time: fast when it works, patient when
    the machine is loaded."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.05)
    return False


class TestLifecycle:
    def test_it_answers_once_started(self, client: httpx.Client) -> None:
        assert client.get("/v1/health").json()["status"] == "ok"

    def test_it_reports_what_startup_did(self, service: LocalService) -> None:
        assert not service.report.degraded

    def test_stopping_releases_the_port(
        self, settings: ServiceSettings, paths: PlatformPaths
    ) -> None:
        service = LocalService(settings, paths=paths)
        service.start()
        url = service.base_url
        service.stop()
        with pytest.raises(httpx.HTTPError), httpx.Client(timeout=2.0) as client:
            client.get(f"{url}/v1/health")

    def test_stopping_twice_is_safe(self, settings: ServiceSettings, paths: PlatformPaths) -> None:
        service = LocalService(settings, paths=paths)
        service.start()
        service.stop()
        service.stop()

    def test_it_restarts(self, settings: ServiceSettings, paths: PlatformPaths) -> None:
        # A restart is what a user does after editing config, and what systemd
        # does on failure. It must not need the process to be recreated.
        first = LocalService(settings, paths=paths)
        first.start()
        first.stop()
        second = LocalService(settings, paths=paths)
        second.start()
        try:
            with httpx.Client(base_url=second.base_url, timeout=TIMEOUT) as client:
                assert client.get("/v1/health").status_code == 200
        finally:
            second.stop()

    def test_the_context_manager_stops_it(
        self, settings: ServiceSettings, paths: PlatformPaths
    ) -> None:
        with LocalService(settings, paths=paths) as service:
            url = service.base_url
        with pytest.raises(httpx.HTTPError), httpx.Client(timeout=2.0) as client:
            client.get(f"{url}/v1/health")


class TestItBindsLoopbackOnly:
    """core/05 section 12, checked against the socket rather than the config."""

    def test_the_bound_address_is_loopback(self, service: LocalService) -> None:
        host, _ = service.bound
        assert ipaddress.ip_address(host).is_loopback

    def test_it_did_not_bind_every_interface(self, service: LocalService) -> None:
        # The wildcard addresses are the ones section 12 forbids by name.
        # Asserting on the socket catches a bind that the configuration
        # allowed and the server then widened.
        host, _ = service.bound
        assert host not in {"0.0.0.0", "::"}

    def test_a_service_cannot_be_configured_for_a_routable_address(self, vault: Path) -> None:
        # Belt and braces: the endpoint type refuses before a socket exists.
        with pytest.raises(ConfigurationError):
            ServiceSettings(vault=vault, endpoint=ServiceEndpoint(host="0.0.0.0"))


class TestConcurrentReads:
    def test_two_reads_overlap(self, service: LocalService) -> None:
        # WAL plus a connection per request. A single shared connection would
        # serialise these, and a `check_same_thread` connection would refuse
        # them outright.
        results: list[int] = []
        errors: list[BaseException] = []

        def read() -> None:
            try:
                with httpx.Client(
                    base_url=service.base_url,
                    headers={"Authorization": f"Bearer {service.runtime.credential}"},
                    timeout=TIMEOUT,
                ) as client:
                    results.append(
                        client.post("/v1/concepts/search", json={"query": "a"}).status_code
                    )
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=read) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=TIMEOUT)

        assert errors == []
        assert results == [200] * 8


class TestTheWatcher:
    def test_a_new_note_is_indexed_without_being_asked(
        self, client: httpx.Client, vault: Path
    ) -> None:
        # The end-to-end claim: an event fires, the debounce settles, and the
        # maintenance thread reconciles.
        assert (
            client.post("/v1/concepts/search", json={"query": "photosynthesis"}).json()["results"]
            == []
        )
        (vault / "30_Knowledge" / "Notes" / "photosynthesis.md").write_text(
            "---\n"
            "type: knowledge\n"
            "id: 01920000-0000-7000-8000-0000000000bb\n"
            "schema: never4ga/0.1\n"
            "title: Photosynthesis\n"
            "created_at: 2026-08-23T10:00:00Z\n"
            "---\n\nPlants turn light into sugar.\n"
        )

        def found() -> bool:
            body = client.post("/v1/concepts/search", json={"query": "photosynthesis"}).json()
            return bool(body["results"])

        assert _until(found), "the watcher never picked up the new note"

    def test_a_change_in_a_noisy_directory_is_ignored(
        self, client: httpx.Client, vault: Path, service: LocalService
    ) -> None:
        # details/data-indexing-maintenance.md section 15. Nothing to observe
        # directly, so observe the consequence: no reconciliation is queued.
        (vault / ".git").mkdir(exist_ok=True)
        (vault / ".git" / "index").write_text("noise")
        (vault / ".obsidian").mkdir(exist_ok=True)
        (vault / ".obsidian" / "workspace.json").write_text("{}")
        time.sleep(0.5)
        assert not service.runtime.loop.coalescer.pending


class TestReconciliationRecoversAMissedEvent:
    def test_an_edit_the_watcher_never_saw_is_still_indexed(
        self, settings: ServiceSettings, paths: PlatformPaths, vault: Path
    ) -> None:
        # details/data-indexing-maintenance.md section 16: reconciliation, not
        # the watcher, is the guarantee. The vault is edited with no watcher
        # running at all, which is what a missed inotify event and a service
        # that was stopped both look like.
        def no_watcher() -> Observer:
            raise OSError("no watcher for this test")

        from never4ga.service.runtime import Observer, VaultRuntime

        runtime = VaultRuntime(settings, paths=paths, observer_factory=no_watcher)
        service = LocalService(settings, paths=paths, runtime=runtime)
        service.start()
        try:
            assert service.report.degraded
            (vault / "30_Knowledge" / "Notes" / "unseen.md").write_text(
                "---\n"
                "type: knowledge\n"
                "id: 01920000-0000-7000-8000-0000000000cc\n"
                "schema: never4ga/0.1\n"
                "title: Unseen\n"
                "created_at: 2026-08-23T10:00:00Z\n"
                "---\n\nWritten while nothing was watching.\n"
            )
            # Periodic reconciliation is what finds it. Reaching the interval
            # in a test means asking the loop what it would do at a later time
            # rather than waiting for one.
            runtime.tick(now=time.monotonic() + settings.reconcile_interval + 1)

            with httpx.Client(
                base_url=service.base_url,
                headers={"Authorization": f"Bearer {runtime.credential}"},
                timeout=TIMEOUT,
            ) as client:
                body = client.post("/v1/concepts/search", json={"query": "unseen"}).json()
            assert body["results"]
        finally:
            service.stop()


class TestTheCredentialStaysOutOfTheLogs:
    def test_starting_the_service_logs_no_credential(
        self,
        settings: ServiceSettings,
        paths: PlatformPaths,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # details/security-configuration.md section 9: "debug mode must not
        # casually disable redaction", so the check runs at DEBUG.
        with caplog.at_level(logging.DEBUG):
            service = LocalService(settings, paths=paths)
            service.start()
            try:
                credential = service.runtime.credential
                with httpx.Client(base_url=service.base_url, timeout=TIMEOUT) as client:
                    client.get("/v1/health")
                    client.get("/v1/vault", headers={"Authorization": f"Bearer {credential}"})
            finally:
                service.stop()
        assert credential not in caplog.text
