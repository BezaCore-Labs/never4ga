"""`document_names` and `record_names` must give the same answer.

The settled reconcile pass resolves a changed document's bare wikilinks
against names reconstructed from the metadata projection, because parsing
every unchanged neighbour to learn its name is the cost the pass exists to
avoid. That only works if projection loses nothing the name resolver reads, so
this suite feeds the same documents to both functions and requires identical
answers.
"""

from __future__ import annotations

from typing import Any

import pytest

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.indexing.projection import document_names, project, record_names


def make(**frontmatter: Any) -> StoredDocument:
    base: dict[str, Any] = {
        "type": "knowledge",
        "id": str(ConceptId.new()),
        "schema": "never4ga/0.1",
        "title": "Thing",
        "created_at": "2026-08-31T12:00:00Z",
    }
    base.update(frontmatter)
    return StoredDocument(
        concept_id=ConceptId.parse(base["id"]),
        path=VaultPath.parse("30_Knowledge/Notes/hybrid-retrieval.md"),
        frontmatter=base,
        body="# Thing\nbody\n",
    )


@pytest.mark.parametrize(
    "frontmatter",
    [
        {},
        {"aliases": ["Old Faithful", "Retrieval Notes"]},
        {"aliases": "One Scalar Alias"},
        {"aliases": ["  padded  ", ""]},
        {"aliases": [42, "Mixed"]},
        {"aliases": None},
    ],
    ids=["bare", "list", "scalar", "padding", "mixed-types", "null"],
)
def test_projection_loses_no_name(frontmatter: dict[str, Any]) -> None:
    document = make(**frontmatter)
    projected = project(document)
    assert record_names(projected.record) == document_names(document)


def test_an_alias_repeating_the_stem_is_one_name() -> None:
    """A document answers to a name once, however many ways it claims it.

    Writing `aliases: [ollama]` on `ollama.md` is natural. If the repeated
    name were kept twice, name lookup would see two candidates, report an
    ambiguous link naming one document, and resolve every link to it to
    nothing. The ambiguity rule is about two documents answering to one name,
    not one document answering to it twice.
    """
    document = make(aliases=["hybrid-retrieval", "HYBRID-RETRIEVAL", "Hybrid Retrieval"])
    assert document_names(document) == ("hybrid-retrieval", "Hybrid Retrieval")
    assert record_names(project(document).record) == document_names(document)
