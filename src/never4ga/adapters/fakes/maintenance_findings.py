"""An in-memory MaintenanceFindings, for tests and for a vault with no database."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from never4ga.ports.maintenance_findings import MaintenanceFinding

__all__ = ["InMemoryMaintenanceFindings"]


class InMemoryMaintenanceFindings:
    """A MaintenanceFindings over a list.

    Closed findings are kept rather than dropped, so the store can answer "has
    this been resolved before" the same way the SQLite one does. Nothing reads
    that yet; keeping them costs a list entry and losing them would make the two
    implementations disagree about a question somebody will eventually ask.
    """

    def __init__(self) -> None:
        self._findings: list[MaintenanceFinding] = []

    def open_findings(self) -> Sequence[MaintenanceFinding]:
        return sorted(
            (f for f in self._findings if f.is_open),
            key=lambda f: (f.detected_at, f.fingerprint),
        )

    def record(self, finding: MaintenanceFinding) -> None:
        if any(f.fingerprint == finding.fingerprint and f.is_open for f in self._findings):
            return
        self._findings.append(replace(finding, resolved_at=None))

    def resolve(self, fingerprint: str, at: datetime) -> None:
        self._findings = [
            replace(f, resolved_at=at) if f.fingerprint == fingerprint and f.is_open else f
            for f in self._findings
        ]

    def clear(self) -> None:
        self._findings.clear()
