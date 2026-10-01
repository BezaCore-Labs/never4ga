"""The ledger that turns each run of `doctor` into a finding's history.

`details/data-indexing-maintenance.md` section 20 gives a finding a
``detected_at`` and a ``resolved_at``, which only mean something if consecutive
runs can tell that two findings are the same problem. Detection has no memory:
it reads the vault and produces a fresh :class:`~never4ga.services.doctor.Finding`
every time. This module is where the memory lives.

The rule: **re-detection resolves.** A finding whose rule stops firing is
closed automatically, because the only evidence that a problem is gone is that
looking for it no longer finds it. A finding a person judges *wrong* is a
different act -- a judgement -- and a judgement lives in the vault where a
rebuild cannot erase it (`core/02` section 21.22). Nothing here dismisses
anything.

This is a service rather than a port method on purpose. Reconciliation is one
rule, and a store that implemented it would be a store that had to be written
correctly once per backend (`core/06` section 22's whole complaint).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from never4ga.domain.document import VaultPath
from never4ga.layout import DOMAIN_REGISTRY
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.maintenance_findings import MaintenanceFinding, MaintenanceFindings
from never4ga.ports.metadata_index import MetadataIndex, MetadataQuery
from never4ga.schema import ValidationLevel, registered_domains, validate_document
from never4ga.services.authoring import Clock, utc_now
from never4ga.services.doctor import Diagnosis, Finding

__all__ = [
    "LedgerUpdate",
    "MaintenanceLedger",
    "fingerprint",
    "validate_changed",
]


def fingerprint(finding: Finding) -> str:
    """What makes two findings from different runs the same problem.

    Rule, path and message. The message is included because one rule fires more
    than once on one document and the instances are genuinely different
    problems -- an `AGENTS.md` naming two paths that are gone is two findings,
    and resolving one should not resolve the other.

    It is a hash rather than the joined text so the column has a bounded width
    and no quoting rules, and it is truncated because this distinguishes
    findings within one vault rather than defending against an adversary.
    """
    path = "" if finding.path is None else str(finding.path)
    material = "\x00".join((finding.code, path, finding.message))
    return hashlib.sha256(material.encode()).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class LedgerUpdate:
    """What one reconciliation changed."""

    opened: tuple[MaintenanceFinding, ...]
    resolved: tuple[MaintenanceFinding, ...]
    still_open: tuple[MaintenanceFinding, ...]

    @property
    def changed(self) -> bool:
        return bool(self.opened or self.resolved)


class MaintenanceLedger:
    """Records a diagnosis, and closes what the diagnosis no longer reports."""

    def __init__(self, findings: MaintenanceFindings, *, now: Clock = utc_now) -> None:
        self._findings = findings
        self._now = now
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        """Why the last :meth:`record` could not write. Cleared by a success."""
        return self._last_error

    def record(self, diagnosis: Diagnosis) -> LedgerUpdate | None:
        """:meth:`reconcile`, except that a store which cannot be written loses.

        Recording makes `doctor` a *writer*. If the running service holds a
        long write transaction when a `doctor` request arrives, the store can
        answer ``database is locked``, and the finished diagnosis must not be
        thrown away because of it.

        `core/06` section 2 settles it: a diagnosis is a reading of canonical
        Markdown and is complete before this is called. The ledger is derived,
        the finding is re-derivable by looking again, and no derived store may
        cost a caller a canonical answer.

        A **partial** diagnosis still raises. That is a caller bug rather than an
        operational failure, and swallowing it would hide exactly what
        :attr:`Diagnosis.complete` exists to prevent.
        """
        if not diagnosis.complete:
            raise ValueError(
                "a partial diagnosis cannot be reconciled: it would resolve every "
                "finding whose rule it never ran"
            )
        try:
            update = self.reconcile(diagnosis)
        # Broad on purpose: what a store can fail with belongs to the adapter,
        # and a service naming `sqlite3.OperationalError` would be one that only
        # worked for one backend.
        except Exception as error:
            self._last_error = str(error)
            return None
        self._last_error = None
        return update

    def reconcile(self, diagnosis: Diagnosis) -> LedgerUpdate:
        """Bring the store into line with one complete diagnosis.

        The argument must be a *complete* diagnosis. Reconciling against a
        partial one would resolve every finding it did not look for, which is
        the difference between "this is fixed" and "nobody checked".

        Refused rather than skipped: a caller arriving here with a partial
        diagnosis has wired something wrongly, and a silent no-op would leave
        that looking like a vault with nothing to report.
        """
        if not diagnosis.complete:
            raise ValueError(
                "a partial diagnosis cannot be reconciled: it would resolve every "
                "finding whose rule it never ran"
            )
        moment = self._now()
        current = {fingerprint(f): f for f in diagnosis.findings if not f.ephemeral}
        before = {f.fingerprint: f for f in self._findings.open_findings()}

        resolved = []
        for mark, stored in before.items():
            if mark not in current:
                self._findings.resolve(mark, moment)
                resolved.append(stored)

        opened = []
        for mark, finding in current.items():
            if mark in before:
                continue
            record = _as_record(finding, mark, moment)
            self._findings.record(record)
            opened.append(record)

        return LedgerUpdate(
            opened=tuple(opened),
            resolved=tuple(resolved),
            still_open=tuple(f for mark, f in before.items() if mark in current),
        )

    def reconcile_within(
        self, findings: Sequence[Finding], scope: Collection[VaultPath]
    ) -> LedgerUpdate:
        """Reconcile, but only for the documents this run actually looked at.

        :meth:`reconcile` resolves by absence, which is a true statement only
        about a complete diagnosis. The immediate tier is the opposite: it looks
        at the few documents that changed, and its silence about the rest of
        the vault means nothing at all.

        So the scope is the paths that were examined. A finding inside it may be
        opened and may be resolved; a finding outside it is untouched.

        **A finding with no path is never in scope**, and therefore is never
        opened here either. `index_is_stale` belongs to the vault rather than to
        a document, and one this tier opened could never be resolved by it --
        no set of paths contains a finding that has none. Those belong to the
        run that looks at everything.
        """
        moment = self._now()
        within = set(scope)
        current = {fingerprint(f): f for f in findings if f.path is not None and f.path in within}
        before = {
            f.fingerprint: f
            for f in self._findings.open_findings()
            if f.path is not None and f.path in within
        }

        resolved = []
        for mark, stored in before.items():
            if mark not in current:
                self._findings.resolve(mark, moment)
                resolved.append(stored)

        opened = []
        for mark, finding in current.items():
            if mark in before:
                continue
            record = _as_record(finding, mark, moment)
            self._findings.record(record)
            opened.append(record)

        return LedgerUpdate(
            opened=tuple(opened),
            resolved=tuple(resolved),
            still_open=tuple(f for mark, f in before.items() if mark in current),
        )


def _as_record(finding: Finding, mark: str, moment: datetime) -> MaintenanceFinding:
    return MaintenanceFinding(
        finding_id=str(uuid.uuid7()),
        rule=finding.code,
        severity=finding.severity,
        fingerprint=mark,
        message=finding.message,
        detected_at=moment,
        document_id=finding.document_id,
        path=finding.path,
        details_json=None,
    )


def validate_changed(
    documents: DocumentStore,
    metadata: MetadataIndex,
    paths: Sequence[VaultPath],
    *,
    level: ValidationLevel = ValidationLevel.STRICT,
) -> list[Finding]:
    """Validate only what changed, using the index for what the corpus knew.

    `details/data-indexing-maintenance.md` §22's immediate tier -- schema,
    link and relation validation on every change.
    :func:`never4ga.services.validation.validate_vault` cannot serve it: it
    reads the whole corpus however few paths it is asked about, because Schema
    v0.1 validation is *relational*. A relation target is checked against every
    id in the vault, and a domain against the registry.

    Both are available without touching the corpus. The index already holds
    every id -- an empty :class:`MetadataQuery` matches everything -- and the
    registry is one document at a reserved path. That is a few reads rather
    than a walk of the whole corpus.

    **The index must already know about the change.** Validating a new document
    against a set of ids that predates it would report every relation into it as
    broken. So this runs after the reconcile that indexed the batch, never
    before -- an ordering the caller keeps, because nothing here can check it.

    A path with no document is not an error: a batch names what changed, and a
    deletion is a change.
    """
    known = {record.concept_id for record in metadata.query(MetadataQuery())}
    registry = documents.get_by_path(DOMAIN_REGISTRY)
    domains = registered_domains([registry] if registry is not None else [])

    findings: list[Finding] = []
    for path in paths:
        document = documents.get_by_path(path)
        if document is None:
            continue
        report = validate_document(
            document.path,
            document.frontmatter,
            level=level,
            known_ids=known,
            domains=domains,
        )
        findings.extend(
            Finding(
                issue.code,
                f"{report.path}: {issue.message}",
                issue.severity,
                report.path,
                issue.repair_hint,
                document.concept_id,
            )
            for issue in report.issues
        )
    return findings
