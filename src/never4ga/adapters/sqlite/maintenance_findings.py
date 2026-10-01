"""SQLiteMaintenanceFindings -- the index database's findings table.

`details/data-indexing-maintenance.md` section 20 defines the table.

The reconciliation rule lives in the schema rather than in this class: a partial
unique index over ``fingerprint WHERE resolved_at IS NULL`` means the database
itself allows at most one open finding per fingerprint, and any number of
resolved ones. So :meth:`record` is an insert that ignores a conflict, and
"already open" needs no read to detect.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.maintenance_findings import MaintenanceFinding
from never4ga.schema import Severity

__all__ = ["SQLiteMaintenanceFindings"]

_RECORD: Final = """
INSERT INTO maintenance_findings
    (finding_id, rule, severity, fingerprint, message,
     document_id, path, detected_at, resolved_at, details_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
ON CONFLICT DO NOTHING
"""

_OPEN: Final = """
SELECT finding_id, rule, severity, fingerprint, message,
       document_id, path, detected_at, resolved_at, details_json
FROM maintenance_findings
WHERE resolved_at IS NULL
ORDER BY detected_at, fingerprint
"""

_RESOLVE: Final = """
UPDATE maintenance_findings
SET resolved_at = ?
WHERE fingerprint = ? AND resolved_at IS NULL
"""


class SQLiteMaintenanceFindings:
    """A MaintenanceFindings over the ``maintenance_findings`` table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def open_findings(self) -> Sequence[MaintenanceFinding]:
        return [_finding(row) for row in self._connection.execute(_OPEN)]

    def record(self, finding: MaintenanceFinding) -> None:
        self._connection.execute(
            _RECORD,
            (
                finding.finding_id,
                finding.rule,
                str(finding.severity),
                finding.fingerprint,
                finding.message,
                None if finding.document_id is None else str(finding.document_id),
                None if finding.path is None else str(finding.path),
                finding.detected_at.isoformat(),
                finding.details_json,
            ),
        )

    def resolve(self, fingerprint: str, at: datetime) -> None:
        self._connection.execute(_RESOLVE, (at.isoformat(), fingerprint))

    def clear(self) -> None:
        self._connection.execute("DELETE FROM maintenance_findings")


def _finding(row: sqlite3.Row) -> MaintenanceFinding:
    resolved = row["resolved_at"]
    document = row["document_id"]
    path = row["path"]
    return MaintenanceFinding(
        finding_id=row["finding_id"],
        rule=row["rule"],
        severity=Severity(row["severity"]),
        fingerprint=row["fingerprint"],
        message=row["message"],
        detected_at=datetime.fromisoformat(row["detected_at"]),
        document_id=None if document is None else ConceptId.parse(document),
        path=None if path is None else VaultPath.parse(path),
        resolved_at=None if resolved is None else datetime.fromisoformat(resolved),
        details_json=row["details_json"],
    )
