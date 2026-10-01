"""A note in foreign material as a Context Pack item (core/02 section 3.3).

A focused or deep pack -- or a startup pack a task retrieved for -- can reach a
note nobody has adopted yet, because the lexical lane reaches the pile. What
reaches the pack is the note as it is now: its path, what the writer called
it, its body, and the reason `foreign_material`. Never an `id`, and never a
structural place: a pile belongs to no workspace, so nothing but retrieval
puts it in a pack.
"""

from __future__ import annotations

from never4ga.context.fusion import FusedCandidate
from never4ga.domain.context import ContextItem, PackCategory
from never4ga.ports.document_store import DocumentStore, ForeignMaterialStore

__all__ = ["foreign_item"]


def foreign_item(
    candidate: FusedCandidate, documents: DocumentStore | None, *, priority: int
) -> ContextItem | None:
    """The item a fused foreign note becomes, or ``None`` when it is gone.

    Without a store to read it from, the item is a reference: the path and a
    name read from it, so the reader still learns the note exists.
    """
    path = candidate.source_path
    if path is None:
        return None
    # `is not None` first: mypy cannot narrow the Optional union straight
    # to the intersection of two protocols, and calls the branch unreachable.
    if documents is not None and isinstance(documents, ForeignMaterialStore):
        note = documents.get_foreign_note(path)
        if note is None:
            # Deleted or adopted since the index saw it; the next pass forgets it.
            return None
        return ContextItem(
            concept_id=None,
            path=path,
            title=note.title,
            priority=priority,
            reason=candidate.reasons[0],
            body=note.body,
            category=PackCategory.RETRIEVED,
        )
    return ContextItem(
        concept_id=None,
        path=path,
        title=path.name.removesuffix(".md"),
        priority=priority,
        reason=candidate.reasons[0],
        is_reference=True,
        category=PackCategory.RETRIEVED,
    )
