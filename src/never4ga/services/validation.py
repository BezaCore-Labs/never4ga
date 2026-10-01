"""Validating a vault, once, for every interface that offers it.

`details/api-cli-mcp-contract.md` section 1 makes the CLI, the HTTP API and MCP
thin clients over the same application services, and none may carry its own
business logic.

A document whose frontmatter will not parse is not yielded by
``iter_documents``, so walking the store alone would never validate or count
it, and a vault with broken files would report every file valid. The store
reports those through :class:`IntegrityReportingStore`, as `doctor` asks it.
This asks the same question in one place, so the three interfaces cannot drift
apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from never4ga.domain.document import VaultPath
from never4ga.ports.document_store import DocumentStore, IntegrityReportingStore
from never4ga.schema import (
    Severity,
    ValidationIssue,
    ValidationLevel,
    ValidationReport,
    registered_domains,
    validate_document,
)

#: core/02 section 23: the file is left exactly as it is, and repair is explicit.
_UNREADABLE_HINT = "the file is left exactly as it is; repair it explicitly (core/02 section 23)"


def validate_vault(
    documents: DocumentStore,
    *,
    level: ValidationLevel,
    paths: Sequence[str] | None = None,
) -> list[ValidationReport]:
    """Every concept in the vault, plus every file that could not be read as one.

    ``paths`` narrows the result to those vault-relative paths; the type
    registry is still read from the whole vault first, so validating one
    document validates it against the whole vocabulary.
    """
    concepts = list(documents.iter_documents())
    known = {document.concept_id for document in concepts}
    domains = registered_domains(concepts)

    wanted = {VaultPath.parse(raw) for raw in paths} if paths else None
    if wanted is not None:
        concepts = [document for document in concepts if document.path in wanted]

    reports = [
        validate_document(
            document.path,
            document.frontmatter,
            level=level,
            known_ids=known,
            domains=domains,
        )
        for document in concepts
    ]
    reports.extend(_unreadable(documents, level, wanted))
    reports.sort(key=lambda report: str(report.path))
    return reports


def _unreadable(
    documents: DocumentStore,
    level: ValidationLevel,
    wanted: set[VaultPath] | None,
) -> Iterable[ValidationReport]:
    """Files that look like concepts and cannot be treated as one.

    A store that cannot hold unreadable material -- an in-memory one, say --
    does not offer the protocol, and answers nothing rather than lying.
    """
    if not isinstance(documents, IntegrityReportingStore):
        return ()
    return (
        ValidationReport(
            problem.path,
            level,
            (
                ValidationIssue(
                    problem.code,
                    problem.detail,
                    Severity.ERROR,
                    None,
                    _UNREADABLE_HINT,
                ),
            ),
        )
        for problem in documents.problems()
        if wanted is None or problem.path in wanted
    )
