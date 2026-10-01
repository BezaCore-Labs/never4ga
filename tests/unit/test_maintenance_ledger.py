"""The ledger that gives a finding a lifetime.

`details/data-indexing-maintenance.md` section 20. A finding that is no longer
detected is resolved, and a dismissal is a separate, canonical act that
nothing here performs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from never4ga.adapters.fakes import InMemoryMaintenanceFindings
from never4ga.domain.document import VaultPath
from never4ga.ports.maintenance_findings import MaintenanceFinding
from never4ga.schema import Severity
from never4ga.services.doctor import Diagnosis, Finding
from never4ga.services.maintenance import MaintenanceLedger, fingerprint

FIRST = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 30, 13, 0, tzinfo=UTC)


def a_finding(
    code: str = "broken_link",
    message: str = "names a target that is not there",
    path: str | None = "30_Knowledge/Notes/thing.md",
    severity: Severity = Severity.WARNING,
) -> Finding:
    return Finding(
        code=code,
        message=message,
        severity=severity,
        path=None if path is None else VaultPath.parse(path),
    )


def a_diagnosis(*findings: Finding, complete: bool = True) -> Diagnosis:
    return Diagnosis(vault_id=None, concept_count=10, findings=findings, complete=complete)


@pytest.fixture
def store() -> InMemoryMaintenanceFindings:
    return InMemoryMaintenanceFindings()


def ledger(store: InMemoryMaintenanceFindings, at: datetime) -> MaintenanceLedger:
    return MaintenanceLedger(store, now=lambda: at)


class TestFingerprint:
    def test_the_same_problem_fingerprints_the_same(self) -> None:
        assert fingerprint(a_finding()) == fingerprint(a_finding())

    def test_a_different_rule_is_a_different_problem(self) -> None:
        assert fingerprint(a_finding()) != fingerprint(a_finding(code="misplaced_document"))

    def test_a_different_document_is_a_different_problem(self) -> None:
        assert fingerprint(a_finding()) != fingerprint(a_finding(path="30_Knowledge/Notes/o.md"))

    def test_one_rule_firing_twice_on_one_document_is_two_problems(self) -> None:
        # An AGENTS.md naming two paths that are gone is two findings, and
        # fixing one must not resolve the other. The message is what tells them
        # apart, which is why it is in the fingerprint.
        assert fingerprint(a_finding(message="names 'a.md'")) != fingerprint(
            a_finding(message="names 'b.md'")
        )

    def test_a_finding_with_no_path_fingerprints_stably(self) -> None:
        assert fingerprint(a_finding(path=None)) == fingerprint(a_finding(path=None))


class TestReconciling:
    def test_a_new_finding_is_opened(self, store: InMemoryMaintenanceFindings) -> None:
        update = ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        assert len(update.opened) == 1
        assert len(store.open_findings()) == 1

    def test_it_is_dated_from_the_run_that_found_it(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        (stored,) = store.open_findings()
        assert stored.detected_at == FIRST

    def test_the_same_finding_next_run_is_not_reopened(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        update = ledger(store, LATER).reconcile(a_diagnosis(a_finding()))
        assert update.opened == ()
        assert len(update.still_open) == 1
        assert len(store.open_findings()) == 1

    def test_and_it_keeps_the_date_it_first_appeared(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        # The whole reason to persist. "Wrong since Tuesday" is a different
        # statement from "something looked a minute ago", and a scheduled
        # diagnosis looks often.
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        ledger(store, LATER).reconcile(a_diagnosis(a_finding()))
        (stored,) = store.open_findings()
        assert stored.detected_at == FIRST

    def test_a_finding_the_diagnosis_no_longer_reports_is_resolved(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        update = ledger(store, LATER).reconcile(a_diagnosis())
        assert len(update.resolved) == 1
        assert list(store.open_findings()) == []

    def test_resolving_one_leaves_the_others_alone(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        fixed = a_finding(message="names 'a.md'")
        remaining = a_finding(message="names 'b.md'")
        ledger(store, FIRST).reconcile(a_diagnosis(fixed, remaining))
        ledger(store, LATER).reconcile(a_diagnosis(remaining))
        (stored,) = store.open_findings()
        assert stored.message == "names 'b.md'"

    def test_a_problem_that_comes_back_is_open_again_and_newly_dated(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        ledger(store, LATER).reconcile(a_diagnosis())
        ledger(store, LATER).reconcile(a_diagnosis(a_finding()))
        (stored,) = store.open_findings()
        assert stored.detected_at == LATER

    def test_a_clean_diagnosis_against_an_empty_store_changes_nothing(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        update = ledger(store, FIRST).reconcile(a_diagnosis())
        assert not update.changed

    def test_reconciling_the_same_diagnosis_twice_changes_nothing_the_second_time(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        # Idempotence is what lets a scheduler run this on a timer without
        # the caller remembering what it has already recorded.
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        update = ledger(store, LATER).reconcile(a_diagnosis(a_finding()))
        assert not update.changed

    def test_errors_and_warnings_are_both_recorded(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        ledger(store, FIRST).reconcile(
            a_diagnosis(
                a_finding(code="duplicate_id", severity=Severity.ERROR),
                a_finding(severity=Severity.WARNING),
            )
        )
        assert {f.severity for f in store.open_findings()} == {
            Severity.ERROR,
            Severity.WARNING,
        }

    def test_the_rule_and_message_are_carried_through(
        self, store: InMemoryMaintenanceFindings
    ) -> None:
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        (stored,) = store.open_findings()
        assert stored.rule == "broken_link"
        assert stored.message == "names a target that is not there"
        assert stored.path == VaultPath.parse("30_Knowledge/Notes/thing.md")


class TestAPartialDiagnosis:
    """A run that did not look at everything must not resolve anything.

    The ledger resolves by absence, so a partial diagnosis reconciled against
    the store would close every finding whose rule the run never ran. That is
    recorded as "fixed" and it is not: nobody checked. Refusing is louder than
    skipping, because a caller that reaches here with a partial diagnosis has a
    wiring bug rather than a nothing-to-do.
    """

    def test_reconciling_one_is_refused(self, store: InMemoryMaintenanceFindings) -> None:
        with pytest.raises(ValueError, match="partial diagnosis"):
            ledger(store, FIRST).reconcile(a_diagnosis(a_finding(), complete=False))

    def test_and_it_writes_nothing_on_the_way_out(self, store: InMemoryMaintenanceFindings) -> None:
        ledger(store, FIRST).reconcile(a_diagnosis(a_finding()))
        with pytest.raises(ValueError):
            ledger(store, LATER).reconcile(a_diagnosis(complete=False))
        (stored,) = store.open_findings()
        assert stored.detected_at == FIRST


class TestWhenTheStoreCannotBeWritten:
    """Remembering a diagnosis must never cost you the diagnosis.

    Recording findings makes `doctor` a writer. When the service's own
    reconcile holds a long write transaction, the ledger write can fail with
    `database is locked`, and the diagnosis must not be thrown away with it.

    `core/06` §2 is the rule: the vault is canonical and the derived store is
    derived. A diagnosis is a reading of canonical Markdown, and it is complete
    before the ledger is touched. Failing to write it down is worth reporting
    and is never worth failing the read for -- the finding will be recorded on
    the next run, which is exactly what a re-derivable observation is for.

    :meth:`reconcile` still raises on a *partial* diagnosis. That is a caller
    bug, not an operational failure, and swallowing it would hide the thing
    `Diagnosis.complete` exists to prevent.
    """

    class Unwritable(InMemoryMaintenanceFindings):
        def record(self, finding: MaintenanceFinding) -> None:
            raise RuntimeError("database is locked")

    def test_record_returns_the_diagnosis_anyway(self) -> None:
        ledger = MaintenanceLedger(self.Unwritable(), now=lambda: FIRST)
        assert ledger.record(a_diagnosis(a_finding())) is None

    def test_and_says_what_went_wrong(self) -> None:
        ledger = MaintenanceLedger(self.Unwritable(), now=lambda: FIRST)
        ledger.record(a_diagnosis(a_finding()))
        assert ledger.last_error is not None
        assert "locked" in ledger.last_error

    def test_a_success_clears_the_error(self, store: InMemoryMaintenanceFindings) -> None:
        ledger = MaintenanceLedger(self.Unwritable(), now=lambda: FIRST)
        ledger.record(a_diagnosis(a_finding()))
        working = MaintenanceLedger(store, now=lambda: LATER)
        working.record(a_diagnosis(a_finding()))
        assert working.last_error is None

    def test_a_working_store_still_records(self, store: InMemoryMaintenanceFindings) -> None:
        update = MaintenanceLedger(store, now=lambda: FIRST).record(a_diagnosis(a_finding()))
        assert update is not None
        assert len(update.opened) == 1

    def test_a_partial_diagnosis_still_raises(self, store: InMemoryMaintenanceFindings) -> None:
        # A caller bug, not an operational failure. Swallowing it would hide
        # the thing `Diagnosis.complete` exists to prevent.
        with pytest.raises(ValueError, match="partial diagnosis"):
            MaintenanceLedger(store, now=lambda: FIRST).record(
                a_diagnosis(a_finding(), complete=False)
            )
