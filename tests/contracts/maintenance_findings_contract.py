"""MaintenanceFindings contract.

core/06 section 22: every backend interface gets one reusable suite, and an
implementation becomes supported by passing it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.maintenance_findings import MaintenanceFinding, MaintenanceFindings
from never4ga.schema import Severity

FIRST = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 30, 13, 0, tzinfo=UTC)


def a_finding(
    *,
    fingerprint: str = "fp-1",
    rule: str = "broken_link",
    severity: Severity = Severity.WARNING,
    message: str = "names a target that is not there",
    detected_at: datetime = FIRST,
    path: str | None = "30_Knowledge/Notes/thing.md",
    document_id: ConceptId | None = None,
    details_json: str | None = None,
) -> MaintenanceFinding:
    # A fresh id every call, deliberately. `core/06` section 3 forbids a row id
    # from carrying meaning, so deriving one from the fingerprint here would
    # test a shape the port rules out -- and would hide the case below where a
    # problem comes back and gets a *new* row.
    return MaintenanceFinding(
        finding_id=str(uuid.uuid7()),
        rule=rule,
        severity=severity,
        fingerprint=fingerprint,
        message=message,
        detected_at=detected_at,
        document_id=document_id,
        path=None if path is None else VaultPath.parse(path),
        details_json=details_json,
    )


class MaintenanceFindingsContract:
    """What every MaintenanceFindings implementation must do."""

    @pytest.fixture
    def findings(self) -> MaintenanceFindings:
        raise NotImplementedError

    def test_an_empty_store_has_nothing_open(self, findings: MaintenanceFindings) -> None:
        assert list(findings.open_findings()) == []

    def test_a_recorded_finding_is_open(self, findings: MaintenanceFindings) -> None:
        findings.record(a_finding())
        (stored,) = findings.open_findings()
        assert stored.fingerprint == "fp-1"
        assert stored.is_open

    def test_every_field_survives_the_round_trip(self, findings: MaintenanceFindings) -> None:
        concept = ConceptId.new()
        findings.record(a_finding(document_id=concept, details_json='{"count": 3}'))
        (stored,) = findings.open_findings()
        assert stored.rule == "broken_link"
        assert stored.severity is Severity.WARNING
        assert stored.message == "names a target that is not there"
        assert stored.detected_at == FIRST
        assert stored.document_id == concept
        assert stored.path == VaultPath.parse("30_Knowledge/Notes/thing.md")
        assert stored.details_json == '{"count": 3}'

    def test_a_finding_may_name_no_document_and_no_path(
        self, findings: MaintenanceFindings
    ) -> None:
        # `index_is_stale` and `repository_absent` are about the vault or a
        # repository rather than about one document, and both are real codes.
        findings.record(a_finding(rule="index_is_stale", path=None))
        (stored,) = findings.open_findings()
        assert stored.path is None
        assert stored.document_id is None

    def test_recording_the_same_fingerprint_twice_opens_one_finding(
        self, findings: MaintenanceFindings
    ) -> None:
        findings.record(a_finding())
        findings.record(a_finding(detected_at=LATER))
        assert len(findings.open_findings()) == 1

    def test_a_finding_is_dated_from_when_it_first_appeared(
        self, findings: MaintenanceFindings
    ) -> None:
        # The whole point of persisting: "this has been wrong since Tuesday",
        # not "something looked at it a minute ago". Detection runs on a timer,
        # so the later record is the common case, not the rare one.
        findings.record(a_finding())
        findings.record(a_finding(detected_at=LATER))
        (stored,) = findings.open_findings()
        assert stored.detected_at == FIRST

    def test_two_fingerprints_are_two_findings(self, findings: MaintenanceFindings) -> None:
        findings.record(a_finding(fingerprint="fp-1"))
        findings.record(a_finding(fingerprint="fp-2"))
        assert {f.fingerprint for f in findings.open_findings()} == {"fp-1", "fp-2"}

    def test_resolving_closes_it(self, findings: MaintenanceFindings) -> None:
        findings.record(a_finding())
        findings.resolve("fp-1", LATER)
        assert list(findings.open_findings()) == []

    def test_resolving_something_absent_is_not_an_error(
        self, findings: MaintenanceFindings
    ) -> None:
        findings.resolve("never-seen", LATER)
        assert list(findings.open_findings()) == []

    def test_resolving_twice_is_not_an_error(self, findings: MaintenanceFindings) -> None:
        findings.record(a_finding())
        findings.resolve("fp-1", LATER)
        findings.resolve("fp-1", LATER)
        assert list(findings.open_findings()) == []

    def test_a_problem_that_comes_back_is_open_again(self, findings: MaintenanceFindings) -> None:
        # And it is dated from when it came back. A resolved finding is a
        # closed record of a past problem; the new one is a new problem that
        # happens to look the same.
        findings.record(a_finding())
        findings.resolve("fp-1", LATER)
        findings.record(a_finding(detected_at=LATER))
        (stored,) = findings.open_findings()
        assert stored.detected_at == LATER

    def test_open_findings_are_ordered_oldest_first(self, findings: MaintenanceFindings) -> None:
        findings.record(a_finding(fingerprint="fp-late", detected_at=LATER))
        findings.record(a_finding(fingerprint="fp-early", detected_at=FIRST))
        assert [f.fingerprint for f in findings.open_findings()] == ["fp-early", "fp-late"]

    def test_findings_detected_together_are_ordered_deterministically(
        self, findings: MaintenanceFindings
    ) -> None:
        findings.record(a_finding(fingerprint="fp-b"))
        findings.record(a_finding(fingerprint="fp-a"))
        assert [f.fingerprint for f in findings.open_findings()] == ["fp-a", "fp-b"]

    def test_clear_drops_everything(self, findings: MaintenanceFindings) -> None:
        findings.record(a_finding(fingerprint="fp-1"))
        findings.record(a_finding(fingerprint="fp-2"))
        findings.clear()
        assert list(findings.open_findings()) == []

    def test_an_error_and_a_warning_both_survive(self, findings: MaintenanceFindings) -> None:
        findings.record(a_finding(fingerprint="fp-e", severity=Severity.ERROR))
        findings.record(a_finding(fingerprint="fp-w", severity=Severity.WARNING))
        assert {f.fingerprint: f.severity for f in findings.open_findings()} == {
            "fp-e": Severity.ERROR,
            "fp-w": Severity.WARNING,
        }
