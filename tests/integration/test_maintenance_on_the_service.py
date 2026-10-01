"""The scheduler, on the service that actually runs it.

Maintenance runs in-process on the existing service, not on a systemd timer.
`tests/unit/test_maintenance_scheduler.py` covers when each tier fires; this
covers that the service wires them to something real and in the right order.

The clock is injected, as everywhere else in the service: a test that waits for
a timer measures the test runner.

**The ordering is the load-bearing part.** The immediate tier validates a
changed document against the ids the *index* holds, so it must run after the
reconcile that indexed the batch. Run it before, and a new document's relations
all resolve to nothing and the tier invents findings on every save. That is
worse than not having the tier, because a rule nobody can trust trains you to
ignore `doctor`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.adapters.sqlite import SQLiteMaintenanceFindings, open_index
from never4ga.config import LocalConfig, MaintenanceSettings, ServiceEndpoint
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.platform_paths import PlatformPaths
from never4ga.ports.maintenance_findings import MaintenanceFinding
from never4ga.service.runtime import ServiceSettings, VaultRuntime
from never4ga.services import VaultInitializer

START = 1000.0


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Scheduled"
    )
    (root / "30_Knowledge" / "Notes").mkdir(parents=True, exist_ok=True)
    return root


class Ticking:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Ticking:
    return Ticking()


@pytest.fixture
def configured(tmp_path: Path) -> Iterator[None]:
    """Cadences short enough to reach by moving the clock, not by waiting."""
    config = LocalConfig.default_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[maintenance]\nfrequent = 10.0\nperiodic = 20.0\n")
    yield


@pytest.fixture
def runtime(vault: Path, clock: Ticking, configured: None) -> Iterator[VaultRuntime]:
    started = VaultRuntime(
        ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0)), clock=clock
    )
    started.start()
    try:
        yield started
    finally:
        started.stop()


def note(vault: Path, name: str, **overrides: str) -> Path:
    """A note, written the way a person writes one: by saving a file.

    ``overrides`` replaces a default rather than adding a second key. A
    duplicate `created_at` is a YAML question, not the schema question these
    tests are asking.
    """
    fields = {
        "type": "knowledge",
        "id": str(ConceptId.new()),
        "schema": "never4ga/0.1",
        "title": name.title(),
        "created_at": '"2026-08-23T12:00:00Z"',
        **overrides,
    }
    rendered = "".join(f"{key}: {value}\n" for key, value in fields.items())
    path = vault / "30_Knowledge" / "Notes" / f"{name}.md"
    path.write_text(f"---\n{rendered}---\n\nbody\n", encoding="utf-8")
    return path


def open_findings(vault: Path) -> list[MaintenanceFinding]:
    documents = FileSystemMarkdownStore(vault)
    manifest = documents.get_by_path(SYSTEM_MANIFEST)
    assert manifest is not None
    database = PlatformPaths.resolve().index_database(manifest.concept_id)
    with closing(open_index(database)) as connection:
        return list(SQLiteMaintenanceFindings(connection).open_findings())


class TestTheImmediateTier:
    def test_a_bad_save_is_reported_when_the_batch_settles(
        self, runtime: VaultRuntime, vault: Path, clock: Ticking
    ) -> None:
        path = note(vault, "broken", created_at="not a timestamp")
        runtime.loop.coalescer.record(path, now=clock.now)
        clock.now += 5
        runtime.tick()
        assert "invalid_timestamp" in {f.rule for f in open_findings(vault)}

    def test_fixing_it_and_saving_again_resolves_it(
        self, runtime: VaultRuntime, vault: Path, clock: Ticking
    ) -> None:
        path = note(vault, "broken", created_at="not a timestamp")
        runtime.loop.coalescer.record(path, now=clock.now)
        clock.now += 5
        runtime.tick()
        assert open_findings(vault) != []

        note(vault, "broken")
        runtime.loop.coalescer.record(path, now=clock.now)
        clock.now += 5
        runtime.tick()
        assert "invalid_timestamp" not in {f.rule for f in open_findings(vault)}

    def test_a_relation_to_a_document_saved_in_the_same_batch_is_not_broken(
        self, runtime: VaultRuntime, vault: Path, clock: Ticking
    ) -> None:
        """The ordering, as a test rather than a comment.

        Both documents are new. If the tier ran before the reconcile, the
        target would not be in the index yet and this would report
        `unresolved_relation_target` on a vault with nothing wrong.
        """
        target = note(vault, "target")
        target_id = FileSystemMarkdownStore(vault).get_by_path(
            VaultPath.parse("30_Knowledge/Notes/target.md")
        )
        assert target_id is not None
        source = vault / "30_Knowledge" / "Notes" / "source.md"
        source.write_text(
            f"---\ntype: knowledge\nid: {ConceptId.new()}\nschema: never4ga/0.1\n"
            'title: Source\ncreated_at: "2026-08-23T12:00:00Z"\n'
            f"relations:\n  - type: superseded_by\n    target: {target_id.concept_id}\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )
        runtime.loop.coalescer.record(target, now=clock.now)
        runtime.loop.coalescer.record(source, now=clock.now)
        clock.now += 5
        runtime.tick()
        assert "unresolved_relation_target" not in {f.rule for f in open_findings(vault)}


class TestThePeriodicTier:
    """The full diagnosis, at a cadence a person would not notice.

    A broken link is not something the immediate tier can see. It is a property
    of the *index* (of what a document points at) rather than of the
    document that was saved, so it is the sweep that finds it.
    """

    @staticmethod
    def _a_broken_link(runtime: VaultRuntime, vault: Path, clock: Ticking) -> None:
        path = note(vault, "thing")
        path.write_text(path.read_text() + "\nsee [x](nowhere.md)\n", encoding="utf-8")
        runtime.loop.coalescer.record(path, now=clock.now)
        clock.now += 5
        runtime.tick()

    def test_the_full_diagnosis_reaches_the_ledger(
        self, runtime: VaultRuntime, vault: Path, clock: Ticking
    ) -> None:
        self._a_broken_link(runtime, vault, clock)
        clock.now += 20
        runtime.tick()
        assert "broken_link" in {f.rule for f in open_findings(vault)}

    def test_it_does_not_run_before_its_cadence(
        self, runtime: VaultRuntime, vault: Path, clock: Ticking
    ) -> None:
        # The immediate tier already ran, and correctly said nothing: the
        # document is valid. Only the sweep knows where its link goes.
        self._a_broken_link(runtime, vault, clock)
        clock.now += 3
        runtime.tick()
        assert "broken_link" not in {f.rule for f in open_findings(vault)}


class TestSwitchingItOff:
    def test_nothing_is_recorded(self, vault: Path, clock: Ticking, tmp_path: Path) -> None:
        config = LocalConfig.default_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("[maintenance]\nenabled = false\n")
        started = VaultRuntime(
            ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0)), clock=clock
        )
        started.start()
        try:
            path = note(vault, "broken", created_at="not a timestamp")
            started.loop.coalescer.record(path, now=clock.now)
            clock.now += 100_000
            started.tick()
        finally:
            started.stop()
        assert open_findings(vault) == []


class TestTheCadenceComesFromTheFile:
    def test_it_is_read_rather_than_hardcoded(self, runtime: VaultRuntime) -> None:
        # details/data-indexing-maintenance.md section 22: "the exact cadence
        # is configurable ... rather than hardcoded".
        assert runtime.maintenance_settings == MaintenanceSettings(
            enabled=True, frequent=10.0, periodic=20.0
        )
