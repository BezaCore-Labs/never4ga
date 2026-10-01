"""A synthetic vault the fixture corpus is measured against.

Small, self-contained and written so that every expectation in
``cases/fixture.toml`` is true by construction: the document a case requires is
the only one that discusses what the case asks about. That is what makes this a
regression gate rather than a relevance measurement -- it proves the lanes still
work and the plumbing still connects, and it says nothing about whether
retrieval is *good*, which only a real vault can answer.

It is deliberately shaped like a real workspace rather than like a word list:
a superseded decision that must not outrank its successor, a document reachable
only through a typed relation, and a `30_Knowledge/` note that a workspace
filter excludes until deep context admits it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.services import VaultInitializer

__all__ = ["WORKSPACE_TITLE", "build_fixture_vault"]

WORKSPACE_TITLE = "Fixture Product"

_CREATED = datetime(2026, 1, 1, tzinfo=UTC).isoformat()

WORKSPACE = "10_Workspaces/Fixture-Product/workspace.md"
SUPERSEDED = "10_Workspaces/Fixture-Product/Decisions/adr-0001_sqlite-index.md"
CURRENT = "10_Workspaces/Fixture-Product/Decisions/adr-0002_postgres-index.md"
RETRIEVAL = "10_Workspaces/Fixture-Product/Context/hybrid-retrieval.md"
DEPLOYMENT = "10_Workspaces/Fixture-Product/Context/deployment.md"
FUSION_NOTE = "30_Knowledge/Notes/reciprocal-rank-fusion.md"


def build_fixture_vault(root: Path) -> dict[str, ConceptId]:
    """Create the vault under ``root`` and return every document's identity."""
    files = FileSystemVaultFileStore(root)
    documents = FileSystemMarkdownStore(root)
    VaultInitializer(files, documents).initialize("Eval Fixture Vault")

    ids = {
        path: ConceptId.new()
        for path in (WORKSPACE, SUPERSEDED, CURRENT, RETRIEVAL, DEPLOYMENT, FUSION_NOTE)
    }
    workspace_id = ids[WORKSPACE]

    def write(path: str, frontmatter: dict[str, Any], body: str) -> None:
        documents.put(
            StoredDocument(
                concept_id=ids[path],
                path=VaultPath.parse(path),
                frontmatter={
                    "type": frontmatter.pop("type"),
                    "id": str(ids[path]),
                    "schema": "never4ga/0.1",
                    "title": frontmatter.pop("title"),
                    "created_at": _CREATED,
                    **frontmatter,
                },
                body=body,
            )
        )

    write(
        WORKSPACE,
        {"type": "workspace", "title": WORKSPACE_TITLE, "workspace_type": "product"},
        "# Fixture Product\n\nA product that exists to be searched.\n",
    )

    write(
        SUPERSEDED,
        {
            "type": "decision",
            "title": "ADR-0001 - SQLite Is the Index Backend",
            "workspace": str(workspace_id),
            "lifecycle": "superseded",
            # core/02 section 17.2: `superseded_by` is the registered relation and
            # it is written on the document that was replaced. `supersedes` is a
            # reverse edge the index MAY derive and MUST NOT be stored, and
            # section 17.3 forbids inventing one when a registered relation exists.
            "relations": [{"type": "superseded_by", "target": str(ids[CURRENT])}],
        },
        "# ADR-0001 - SQLite Is the Index Backend\n\n"
        "The index backend is SQLite. It ships with Python, needs no server and\n"
        "rebuilds from Markdown in seconds. This decision was later reversed.\n",
    )

    write(
        CURRENT,
        {
            "type": "decision",
            "title": "ADR-0002 - PostgreSQL Is the Index Backend",
            "workspace": str(workspace_id),
            "lifecycle": "accepted",
        },
        "# ADR-0002 - PostgreSQL Is the Index Backend\n\n"
        "The index backend is PostgreSQL. Concurrent writers made SQLite the\n"
        "wrong choice once the service owned the index, so this supersedes the\n"
        "earlier decision.\n",
    )

    write(
        RETRIEVAL,
        {
            "type": "context",
            "title": "Hybrid Retrieval",
            "workspace": str(workspace_id),
            "relations": [{"type": "depends_on", "target": str(ids[CURRENT])}],
        },
        "# Hybrid Retrieval\n\n"
        "Retrieval runs several lanes and fuses them by rank. Structural\n"
        "metadata reduces the candidate set before any search runs.\n",
    )

    write(
        DEPLOYMENT,
        {"type": "context", "title": "Deployment", "workspace": str(workspace_id)},
        "# Deployment\n\n"
        "How the product is packaged and shipped. Container images, release\n"
        "tags and the rollback procedure. Nothing here concerns searching.\n",
    )

    write(
        FUSION_NOTE,
        {"type": "knowledge", "title": "Reciprocal Rank Fusion"},
        "# Reciprocal Rank Fusion\n\n"
        "Reciprocal rank fusion sums one over k plus rank across retrievers, so\n"
        "a document several retrievers found independently outranks one a single\n"
        "retriever put first.\n",
    )

    return ids
