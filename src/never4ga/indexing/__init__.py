"""Turning canonical Markdown into the material an index can hold.

Everything in this package is a pure function of a document: the same Markdown
produces the same headings, the same chunk identities and the same edges, on any
machine, in any order. That determinism is what makes the projections in
`never4ga.adapters.sqlite` rebuildable (core/06 section 2) and what lets a chunk
say where it came from (core/06 section 4).

It knows nothing about SQLite, or about any backend. It builds the port types --
``MetadataRecord``, ``IndexedChunk``, ``GraphEdge`` -- and the indexing service
hands them to whichever implementation is configured.
"""

from __future__ import annotations

from never4ga.indexing.chunks import (
    CHUNK_POLICY_VERSION,
    MAX_CHUNK_CHARACTERS,
    build_chunks,
    build_foreign_chunks,
)
from never4ga.indexing.markdown import (
    Heading,
    MarkdownLink,
    Section,
    scan_headings,
    scan_links,
    scan_sections,
)
from never4ga.indexing.projection import (
    DocumentLink,
    DocumentProjection,
    ProjectionIssue,
    document_names,
    project,
    project_foreign,
    written_title,
)

__all__ = [
    "CHUNK_POLICY_VERSION",
    "MAX_CHUNK_CHARACTERS",
    "DocumentLink",
    "DocumentProjection",
    "Heading",
    "MarkdownLink",
    "ProjectionIssue",
    "Section",
    "build_chunks",
    "build_foreign_chunks",
    "document_names",
    "project",
    "project_foreign",
    "scan_headings",
    "scan_links",
    "scan_sections",
    "written_title",
]
