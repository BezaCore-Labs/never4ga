"""Service startup and the reconciliation loop.

core/05 section 18 gives the startup sequence and ends with "any failure must
produce actionable health output"; section 19 adds that a failed *optional*
subsystem must not destroy the service. Those two together are most of what a
daemon has to get right, so the report a startup produces is tested as
carefully as the work it does.

The reconciliation loop is driven by an injected clock rather than by sleeping.
A test that waits for a timer measures the test runner.
"""

from __future__ import annotations

import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.config import ServiceEndpoint
from never4ga.errors import Never4gaError
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.platform_paths import PlatformPaths
from never4ga.service.runtime import (
    Observer,
    ReconcileLoop,
    ServiceSettings,
    VaultRuntime,
)
from never4ga.service.watcher import Coalescer
from never4ga.services import ContentService, VaultInitializer


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents).initialize("Service Vault")
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
    return ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0))


@pytest.fixture
def runtime(settings: ServiceSettings, paths: PlatformPaths) -> Iterator[VaultRuntime]:
    started = VaultRuntime(settings, paths=paths)
    yield started
    started.stop()


class TestStartup:
    """core/05 section 18, step by step."""

    def test_it_reports_every_step(self, runtime: VaultRuntime) -> None:
        report = runtime.start()
        assert [step.name for step in report.steps] == [
            "resolve_vault",
            "validate_manifest",
            "open_index",
            "reconcile",
            "start_watcher",
        ]

    def test_a_healthy_start_is_not_degraded(self, runtime: VaultRuntime) -> None:
        report = runtime.start()
        assert report.ok
        assert not report.degraded

    def test_it_learns_the_vault_identity(self, runtime: VaultRuntime, vault: Path) -> None:
        # core/05 section 6: the system manifest's id *is* the vault identity,
        # and it keys where derived state lives.
        report = runtime.start()
        manifest = FileSystemMarkdownStore(vault).get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        assert report.vault_id == manifest.concept_id

    def test_it_indexes_the_vault(self, runtime: VaultRuntime) -> None:
        # Section 18: reconcile before the API answers, so the first search
        # after a restart is not against an empty index.
        runtime.start()
        with runtime.sessions() as session:
            assert not session.indexer.health().is_stale

    def test_the_database_lands_outside_the_vault(
        self, runtime: VaultRuntime, paths: PlatformPaths, vault: Path
    ) -> None:
        # core/05 section 7.
        report = runtime.start()
        database = paths.index_database(report.vault_id)
        assert database.is_file()
        assert vault not in database.parents

    def test_starting_twice_is_refused_rather_than_silently_ignored(
        self, runtime: VaultRuntime
    ) -> None:
        runtime.start()
        with pytest.raises(Never4gaError):
            runtime.start()

    def test_stopping_releases_the_watcher(self, runtime: VaultRuntime) -> None:
        runtime.start()
        runtime.stop()
        assert not runtime.running

    def test_stopping_twice_is_safe(self, runtime: VaultRuntime) -> None:
        runtime.start()
        runtime.stop()
        runtime.stop()


class TestStartupFailure:
    def test_a_missing_vault_is_fatal_and_says_so(
        self, tmp_path: Path, paths: PlatformPaths
    ) -> None:
        runtime = VaultRuntime(
            ServiceSettings(vault=tmp_path / "nowhere", endpoint=ServiceEndpoint(port=0)),
            paths=paths,
        )
        with pytest.raises(Never4gaError, match="nowhere"):
            runtime.start()

    def test_an_uninitialized_directory_is_fatal(
        self, tmp_path: Path, paths: PlatformPaths
    ) -> None:
        # Without a system manifest there is no vault identity, so derived
        # state has nowhere to live.
        empty = tmp_path / "empty"
        empty.mkdir()
        runtime = VaultRuntime(
            ServiceSettings(vault=empty, endpoint=ServiceEndpoint(port=0)), paths=paths
        )
        with pytest.raises(Never4gaError, match="init"):
            runtime.start()

    def test_a_watcher_that_will_not_start_degrades_rather_than_stops(
        self, settings: ServiceSettings, paths: PlatformPaths
    ) -> None:
        # core/05 section 19. Losing the watcher costs timeliness; periodic
        # reconciliation still keeps the index correct.
        def refuse() -> Observer:
            raise OSError("inotify watch limit reached")

        runtime = VaultRuntime(settings, paths=paths, observer_factory=refuse)
        report = runtime.start()
        assert report.ok
        assert report.degraded
        assert any("inotify" in step.detail for step in report.steps if not step.ok)

    def test_the_service_still_serves_without_a_watcher(
        self, settings: ServiceSettings, paths: PlatformPaths
    ) -> None:
        def refuse() -> Observer:
            raise OSError("inotify watch limit reached")

        runtime = VaultRuntime(settings, paths=paths, observer_factory=refuse)
        runtime.start()
        with runtime.sessions() as session:
            assert not session.indexer.health().is_stale

    def test_an_unreadable_document_does_not_stop_startup(
        self, runtime: VaultRuntime, vault: Path
    ) -> None:
        # Section 19's own example: index the other files and report the one.
        (vault / "30_Knowledge" / "Notes" / "broken.md").write_text(
            "---\nthis: [is not: valid yaml\n---\nbody\n"
        )
        report = runtime.start()
        assert report.ok


class TestTheCredential:
    def test_it_is_generated_on_first_start(self, runtime: VaultRuntime) -> None:
        runtime.start()
        assert runtime.credential

    def test_it_survives_a_restart(self, settings: ServiceSettings, paths: PlatformPaths) -> None:
        first = VaultRuntime(settings, paths=paths)
        first.start()
        credential = first.credential
        first.stop()

        second = VaultRuntime(settings, paths=paths)
        second.start()
        assert second.credential == credential

    def test_it_is_stored_outside_the_vault(
        self, runtime: VaultRuntime, paths: PlatformPaths, vault: Path
    ) -> None:
        runtime.start()
        secrets = paths.state / "secrets.json"
        assert secrets.is_file()
        assert vault not in secrets.parents

    def test_only_its_owner_can_read_it(self, runtime: VaultRuntime, paths: PlatformPaths) -> None:
        runtime.start()
        assert stat.S_IMODE((paths.state / "secrets.json").stat().st_mode) == 0o600

    def test_the_startup_report_does_not_carry_it(self, runtime: VaultRuntime) -> None:
        report = runtime.start()
        assert runtime.credential not in str(report)

    def test_the_fallback_store_warns(self, runtime: VaultRuntime) -> None:
        # details/security-configuration.md section 4.
        report = runtime.start()
        assert any("keyring" in warning for warning in report.warnings)


class TestTheReconcileLoop:
    """Section 16: startup, manual, periodic, and full rebuild."""

    @pytest.fixture
    def calls(self) -> list[bool]:
        return []

    @pytest.fixture
    def loop(self, calls: list[bool]) -> ReconcileLoop:
        return ReconcileLoop(
            coalescer=Coalescer(quiet_period=0.5),
            reconcile=lambda changed_only: calls.append(changed_only),
            interval=300.0,
            started_at=1000.0,
        )

    def test_a_quiet_vault_reconciles_nothing(self, loop: ReconcileLoop, calls: list[bool]) -> None:
        loop.tick(now=1001.0)
        assert calls == []

    def test_a_settled_batch_triggers_a_changed_only_run(
        self, loop: ReconcileLoop, calls: list[bool]
    ) -> None:
        loop.coalescer.record(Path("a.md"), now=1001.0)
        loop.tick(now=1001.2)
        assert calls == []
        loop.tick(now=1002.0)
        assert calls == [True]

    def test_the_interval_triggers_a_run_with_no_events_at_all(
        self, loop: ReconcileLoop, calls: list[bool]
    ) -> None:
        # A watcher can miss an event, and a vault can be edited while the
        # service is stopped. Periodic reconciliation is what recovers both.
        loop.tick(now=1299.0)
        assert calls == []
        loop.tick(now=1301.0)
        assert calls == [True]

    def test_the_interval_restarts_after_any_run(
        self, loop: ReconcileLoop, calls: list[bool]
    ) -> None:
        loop.tick(now=1301.0)
        loop.tick(now=1400.0)
        assert calls == [True]
        loop.tick(now=1602.0)
        assert calls == [True, True]

    def test_a_failing_reconciliation_does_not_stop_the_loop(self) -> None:
        # The loop runs on the service's own thread. An exception escaping it
        # would take the daemon down because one document was malformed.
        attempts: list[float] = []

        def explode(changed_only: bool) -> None:
            attempts.append(1.0)
            raise RuntimeError("index is unhappy")

        loop = ReconcileLoop(
            coalescer=Coalescer(quiet_period=0.5),
            reconcile=explode,
            interval=10.0,
            started_at=0.0,
        )
        loop.tick(now=11.0)
        loop.tick(now=22.0)
        assert len(attempts) == 2

    def test_a_failure_is_recorded_for_health_output(self) -> None:
        def explode(changed_only: bool) -> None:
            raise RuntimeError("index is unhappy")

        loop = ReconcileLoop(
            coalescer=Coalescer(quiet_period=0.5),
            reconcile=explode,
            interval=10.0,
            started_at=0.0,
        )
        loop.tick(now=11.0)
        assert loop.last_error is not None
        assert "unhappy" in loop.last_error


class TestTheServiceSerialisesItsOwnWriters:
    """Writing sessions in one service process never overlap.

    `core/05` section 10 gives the service SQLite write coordination. Three
    writers share one process: the watcher's reconcile loop, the maintenance
    tiers, and whatever an API request asks for. SQLite admits one writer, so
    without coordination the loser gets `database is locked`, which surfaces as
    an HTTP 500 naming the symptom and never the second writer.

    The busy timeout in `adapters/sqlite/connection.py` is not the answer: it
    covers a reader waiting on a checkpoint, not contention between peers.
    """

    def test_two_writing_sessions_do_not_overlap(self, runtime: VaultRuntime) -> None:
        import threading

        runtime.start()

        overlaps: list[int] = []
        entered = 0
        inside = 0
        guard = threading.Lock()
        start = threading.Barrier(2)

        def write() -> None:
            nonlocal inside, entered
            start.wait(timeout=5)
            with runtime.sessions(write=True):
                with guard:
                    inside += 1
                    entered += 1
                    if inside > 1:
                        overlaps.append(inside)
                time.sleep(0.05)
                with guard:
                    inside -= 1

        threads = [threading.Thread(target=write) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not overlaps, "two writing sessions were open at once"
        assert entered == 2, "both threads must actually have opened a session"

    def test_reading_sessions_still_run_alongside_a_writer(self, runtime: VaultRuntime) -> None:
        """Reads are not serialised behind a writer.

        Serialising reads would solve a problem readers do not have and throw
        away what WAL is for: a search would queue behind a whole reconcile.
        """
        import threading

        runtime.start()
        released = threading.Event()
        read_completed = threading.Event()

        def hold_the_write_lock() -> None:
            with runtime.sessions(write=True):
                released.wait(timeout=5)

        writer = threading.Thread(target=hold_the_write_lock)
        writer.start()
        try:
            time.sleep(0.05)  # let the writer take it

            def read() -> None:
                with runtime.sessions():
                    read_completed.set()

            reader = threading.Thread(target=read)
            reader.start()
            reader.join(timeout=5)
            assert read_completed.is_set(), "a read waited on the write lock"
        finally:
            released.set()
            writer.join(timeout=5)

    def test_a_writing_session_may_nest(self, runtime: VaultRuntime) -> None:
        """Reentrant on purpose: a write path may open a nested session."""
        runtime.start()
        with runtime.sessions(write=True), runtime.sessions(write=True) as inner:
            assert inner.metadata is not None


class TestStartupDoesNotHashTheWholeVaultBeforeAnswering:
    """Startup answers before the full re-hash of the vault.

    `core/05` §18 requires a reconcile before the API answers. The *full* pass
    parses and hashes every document, which on a large vault takes minutes, and
    until it finishes the socket is not open at all.

    The full pass is not pointless -- it is the safety net for bytes that
    change while size and mtime do not, which a checkout or a sync tool can
    produce. But it does not have to happen *before* the first answer. Startup
    runs the cheap stat-based pass, and the full re-hash follows on the first
    tick, after the socket is open.

    The window this opens is the one the watcher already lives in, and it is
    seconds rather than never.
    """

    def test_startup_runs_the_cheap_pass(self, runtime: VaultRuntime) -> None:
        report = runtime.start()
        reconcile = next(step for step in report.steps if step.name == "reconcile")
        assert reconcile.ok
        assert "full pass" in reconcile.detail

    def test_the_index_is_usable_the_moment_startup_returns(self, runtime: VaultRuntime) -> None:
        # Cheap must not mean incomplete. A vault indexed for the first time
        # has nothing recorded, so the settled pass has everything to do and
        # does it -- the saving is on a restart, not on a first run.
        runtime.start()
        with runtime.sessions() as session:
            assert not session.indexer.health().is_stale

    def test_the_full_pass_runs_on_the_first_tick(self, runtime: VaultRuntime) -> None:
        runtime.start()
        assert runtime.deep_pass_pending is True
        runtime.tick()
        assert runtime.deep_pass_pending is False

    def test_it_runs_once_and_not_on_every_tick(self, runtime: VaultRuntime) -> None:
        # A full re-hash on every tick would bring the whole cost back, forever,
        # in a background thread instead of at the front.
        runtime.start()
        runtime.tick()
        runtime.tick()
        assert runtime.deep_pass_pending is False

    def test_a_failing_full_pass_does_not_take_the_service_down(
        self, runtime: VaultRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # It runs on the maintenance pump, which outlives a bad document by
        # design. A background repair that could kill a serving process would
        # be worse than the latency it removes.
        runtime.start()

        def explode(changed_only: bool) -> object:
            raise RuntimeError("a document went bad")

        monkeypatch.setattr(runtime, "_reconcile", explode)
        runtime.tick()
        assert runtime.deep_pass_pending is False
