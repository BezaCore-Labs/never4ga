"""A finding survives the run that found it.

Every `doctor` finding persists, resolves on re-detection, and survives a
service restart. The tests run through ``main()`` because that is where a
user's `doctor` goes.

Recording changes nothing in the payload. Persisting is not a vault write:
findings are derived observations (details/data-indexing-maintenance.md
section 20), so they land in the index's own database.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pytest

from never4ga.adapters.sqlite import SQLiteMaintenanceFindings, open_index
from never4ga.cli import main
from never4ga.config import ServiceEndpoint
from never4ga.domain.identity import ConceptId
from never4ga.platform_paths import PlatformPaths
from never4ga.ports.maintenance_findings import MaintenanceFinding
from never4ga.service.runtime import ServiceSettings, VaultRuntime
from never4ga.services.maintenance import MaintenanceLedger

Run = Callable[..., Any]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str) -> Any:
        main(["--vault", str(vault), "--json", *arguments])
        captured = capsys.readouterr()
        return json.loads(captured.out or captured.err)

    return invoke


@pytest.fixture
def initialized(run: Run) -> Run:
    run("init")
    return run


def note(vault: Path, name: str, body: str) -> Path:
    path = vault / "30_Knowledge" / "Notes" / f"{name}.md"
    path.write_text(
        "---\n"
        "type: knowledge\n"
        f"id: {ConceptId.new()}\n"
        "schema: never4ga/0.1\n"
        f"title: {name}\n"
        'created_at: "2026-08-23T12:00:00Z"\n'
        f"---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@contextmanager
def _findings(vault_id: str) -> Iterator[SQLiteMaintenanceFindings]:
    database = PlatformPaths.resolve().index_database(ConceptId.parse(vault_id))
    with closing(open_index(database)) as connection:
        yield SQLiteMaintenanceFindings(connection)


def open_findings(vault_id: str) -> list[MaintenanceFinding]:
    with _findings(vault_id) as store:
        return list(store.open_findings())


def database_of(vault_id: str) -> Path:
    return PlatformPaths.resolve().index_database(ConceptId.parse(vault_id))


class TestPersistence:
    def test_a_finding_is_recorded(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor")
        assert "broken_link" in {f.rule for f in open_findings(payload["vault_id"])}

    def test_it_keeps_the_date_it_first_appeared(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor")
        (first,) = [f for f in open_findings(payload["vault_id"]) if f.rule == "broken_link"]
        initialized("doctor")
        (again,) = [f for f in open_findings(payload["vault_id"]) if f.rule == "broken_link"]
        assert again.detected_at == first.detected_at
        assert again.finding_id == first.finding_id

    def test_fixing_the_problem_resolves_it(self, initialized: Run, vault: Path) -> None:
        path = note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor")
        assert "broken_link" in {f.rule for f in open_findings(payload["vault_id"])}

        path.write_text(
            path.read_text(encoding="utf-8").replace("see [nothing](nowhere.md)", "see nothing"),
            encoding="utf-8",
        )
        initialized("index")
        initialized("doctor")
        assert "broken_link" not in {f.rule for f in open_findings(payload["vault_id"])}

    def test_a_resolved_finding_keeps_its_history(self, initialized: Run, vault: Path) -> None:
        # `resolved_at` is the point of persisting, so a resolved row stays as
        # the record that the problem was there and is not any more.
        path = note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor")
        path.write_text(
            path.read_text(encoding="utf-8").replace("see [nothing](nowhere.md)", "see nothing"),
            encoding="utf-8",
        )
        initialized("index")
        initialized("doctor")
        with closing(sqlite3.connect(database_of(payload["vault_id"]))) as connection:
            resolved = connection.execute(
                "SELECT resolved_at FROM maintenance_findings WHERE rule = 'broken_link'"
            ).fetchall()
        assert resolved and all(row[0] is not None for row in resolved)


class TestWhatIsNotRecorded:
    def test_a_partial_level_records_nothing(self, initialized: Run, vault: Path) -> None:
        # A `core`-level run asks for fewer schema rules, so reconciling it
        # would resolve findings it never looked for.
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor", "--level", "core")
        assert open_findings(payload["vault_id"]) == []

    def test_and_it_does_not_resolve_what_a_full_run_found(
        self, initialized: Run, vault: Path
    ) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor")
        before = open_findings(payload["vault_id"])
        initialized("doctor", "--level", "okf")
        assert open_findings(payload["vault_id"]) == before

    def test_doctor_still_does_not_create_a_database(self, initialized: Run, vault: Path) -> None:
        # `_indexes` opens with ``create=False`` for exactly this reason:
        # derived state is something the user asks for with `index`. Recording
        # findings must not have quietly turned `doctor` into a writer.
        payload = initialized("doctor")
        assert not database_of(payload["vault_id"]).exists()

    def test_the_payload_is_unchanged_by_recording(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        first = initialized("doctor")
        assert first == initialized("doctor")


class TestSurvivingARestart:
    def test_the_findings_outlive_the_service(self, initialized: Run, vault: Path) -> None:
        """Findings survive a service restart, taken literally.

        Two runtimes, started and stopped, with the diagnosis run through the
        session each assembles. A store that lived in the process -- or in a
        connection-scoped cache -- would pass every test above and fail this
        one.
        """
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")

        settings = ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0))
        runtime = VaultRuntime(settings)
        runtime.start()
        try:
            with runtime.sessions() as session:
                session.diagnose()
        finally:
            runtime.stop()

        restarted = VaultRuntime(settings)
        report = restarted.start()
        try:
            with restarted.sessions() as session:
                assert session.findings is not None
                (finding,) = [
                    f for f in session.findings.open_findings() if f.rule == "broken_link"
                ]
                first_seen = finding.detected_at
        finally:
            restarted.stop()

        assert "broken_link" in {f.rule for f in open_findings(str(report.vault_id))}
        assert first_seen is not None


class TestRebuild:
    def test_a_rebuild_leaves_the_ledger_alone(self, initialized: Run, vault: Path) -> None:
        """Findings are keyed by what a rule said, not by what the index holds.

        `rebuild` discards every projection because nothing durable lives only
        in the index (details/data-indexing-maintenance.md section 17), and it
        could discard findings for the same reason -- a finding is re-derivable
        by running detection again. It does not, because there is nothing to
        gain: a fingerprint is rule, path and message, none of which a rebuild
        changes, and clearing would throw away the one thing persisting was
        for. Anything a rebuild *does* fix is resolved by the next run in the
        ordinary way.
        """
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor")
        (before,) = [f for f in open_findings(payload["vault_id"]) if f.rule == "broken_link"]

        initialized("rebuild")
        (after,) = [f for f in open_findings(payload["vault_id"]) if f.rule == "broken_link"]
        assert after.detected_at == before.detected_at


class TestALockedDatabaseNeverCostsTheDiagnosis:
    """A locked findings database never costs the caller the diagnosis.

    Recording findings makes `doctor` a writer. When the running service holds a
    long write transaction, the write fails with ``database is locked``, and that
    must not become an HTTP 500 that throws the computed diagnosis away.

    `core/06` §2: the vault is canonical, the ledger is derived, and no derived
    store may cost a caller a canonical answer. The finding is recorded on the
    next run, which is what a re-derivable observation is for.
    """

    def test_the_diagnosis_still_comes_back(self, initialized: Run, vault: Path) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        payload = initialized("doctor")
        vault_id = payload["vault_id"]
        assert payload["findings"], "there has to be something to write down"

        with _findings(vault_id):
            pass  # the database exists and the schema is applied

        with closing(sqlite3.connect(database_of(vault_id), isolation_level=None)) as held:
            held.execute("BEGIN IMMEDIATE")
            # The vault is canonical and readable; only the bookkeeping is
            # blocked, and the answer must not depend on it.
            blocked = _diagnose(vault)

        assert blocked.concept_count > 0
        assert [f.code for f in blocked.findings]

    def test_and_the_ledger_says_why_it_could_not_write(
        self, initialized: Run, vault: Path
    ) -> None:
        note(vault, "thing", "# One\nsee [nothing](nowhere.md)")
        initialized("index")
        vault_id = initialized("doctor")["vault_id"]

        with closing(sqlite3.connect(database_of(vault_id), isolation_level=None)) as held:
            held.execute("BEGIN IMMEDIATE")
            with _findings(vault_id) as store:
                ledger = MaintenanceLedger(store)
                ledger.record(_diagnose(vault))
                assert ledger.last_error is not None


def _diagnose(vault: Path) -> Any:
    """A complete diagnosis, assembled the way a composition root does."""
    from never4ga.adapters.fakes import FakeRepositoryLocator
    from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
    from never4ga.services.doctor import Doctor
    from never4ga.services.indexing import IndexHealth

    return Doctor(
        FileSystemVaultFileStore(vault),
        FileSystemMarkdownStore(vault),
        index=IndexHealth(),
        repositories=FakeRepositoryLocator(),
        adapters=(),
    ).diagnose()
