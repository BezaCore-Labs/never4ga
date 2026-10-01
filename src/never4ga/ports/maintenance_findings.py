"""MaintenanceFindings -- the store that gives a finding a lifetime.

`details/data-indexing-maintenance.md` section 20 asks for a
``maintenance_findings`` table and lists what one row holds. Without a store,
nothing could say when a problem first appeared, whether it is the same one as
yesterday, or that it has gone.

Section 20 also states the constraint that keeps this honest -- "findings are
derived observations". A finding is *re-derivable from the vault by running
detection again*, which is exactly what makes it belong in the index's own
database rather than in a file of its own: ``rebuild`` discards every projection
and builds it back from Markdown, and doing that to findings is correct. This is
the difference from :mod:`never4ga.ports.tracker_cache`, which caches somebody
else's operational state and therefore cannot live where ``rebuild`` can reach.

`core/06` section 3 applies unchanged: a ``finding_id`` is a derived row id and
is never canonical identity. Delete the whole table and the vault is unharmed.

**Identity across runs is the fingerprint, not the id.** Detection has no memory
between runs, so the same problem produces a *new* :class:`Finding` object each
time. Two of them are the same finding when they carry the same rule, path and
message; that is what :attr:`MaintenanceFinding.fingerprint` records, and it is
why re-detection can resolve a finding rather than accumulating duplicates of it.

**Resolution has two sources and only one of them lives here.** A finding whose
rule stops firing is resolved by this store. A finding a person judges *wrong*
is a judgement, and a judgement lives in the vault as a `finding_dismissal`
(core/02 section 21.22), where a rebuild cannot erase it. This store would only
ever cache such a dismissal, never own it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.schema import Severity

__all__ = ["MaintenanceFinding", "MaintenanceFindings"]


@dataclass(frozen=True, slots=True)
class MaintenanceFinding:
    """One finding, as the store remembers it.

    The fields are `details/data-indexing-maintenance.md` section 20's, with one
    addition. Section 20 lists ``finding_id`` and nothing that says two rows are
    the same problem, which works only if the id is derived from the content --
    and then it is a fingerprint wearing the wrong name, and `core/06` section 3
    forbids a row id from carrying meaning. So the two are separate here:
    :attr:`finding_id` is opaque and unique per row, :attr:`fingerprint` is
    derived and is what matching uses.
    """

    finding_id: str
    rule: str
    severity: Severity
    fingerprint: str
    message: str
    detected_at: datetime
    document_id: ConceptId | None = None
    path: VaultPath | None = None
    resolved_at: datetime | None = None
    details_json: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("detected_at", "resolved_at"):
            moment = getattr(self, field_name)
            # Both instants are Never4gA's own, so they are the ones it can
            # insist on. A naive one compares against an aware "now" by raising
            # somewhere far from here, at whatever moment a reader first asks
            # how old a finding is.
            if moment is not None and moment.tzinfo is None:
                raise ValueError(f"a finding's {field_name} must carry a timezone")

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


@runtime_checkable
class MaintenanceFindings(Protocol):
    def open_findings(self) -> Sequence[MaintenanceFinding]:
        """Every unresolved finding, in a stable order.

        Ordered by ``detected_at`` then ``fingerprint``: oldest problem first,
        deterministic among findings detected in the same run. A caller wanting
        errors before warnings sorts, because a store should not decide what a
        view means.
        """
        ...

    def record(self, finding: MaintenanceFinding) -> None:
        """Open a finding, unless one with the same fingerprint is already open.

        Idempotent by construction, which is what lets detection run on a timer
        without the caller tracking what it has already seen. Recording a
        finding that is already open keeps the original :attr:`detected_at` --
        the problem has been there since it first appeared, not since the last
        time something looked.
        """
        ...

    def resolve(self, fingerprint: str, at: datetime) -> None:
        """Close the open finding with this fingerprint.

        Absent is not an error: resolving is idempotent for the same reason
        recording is.
        """
        ...

    def clear(self) -> None:
        """Drop everything. The next detection run costs nothing but time."""
        ...
