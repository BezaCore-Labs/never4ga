"""A pack says why it carried an item as a reference (core/07 section 10).

A body that does not fit becomes a reference. A document too large ever to fit
needs a person to fix it; one that arrived after the room was spent needs
nothing. So a reference substitution names which of three things caused it.
"""

from __future__ import annotations

from never4ga.context.budget import apply_budget
from never4ga.domain.context import ContextBudget, ContextItem, ReferenceReason
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode


def make_item(body: str | None, priority: int = 10) -> ContextItem:
    return ContextItem(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(f"30_Knowledge/Notes/thing-{priority}.md"),
        title=f"Thing {priority}",
        priority=priority,
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
        body=body,
    )


def fit(
    *items: ContextItem, max_characters: int = 100, max_full_documents: int | None = None
) -> list[ContextItem]:
    budget = ContextBudget(
        max_items=10, max_characters=max_characters, max_full_documents=max_full_documents
    )
    kept, _ = apply_budget(items, budget)
    return list(kept)


class TestReferenceReasons:
    def test_a_carried_body_has_no_reason(self) -> None:
        (item,) = fit(make_item("x" * 10))
        assert item.is_reference is False
        assert item.reference_reason is None

    def test_a_body_larger_than_the_whole_budget(self) -> None:
        (item,) = fit(make_item("x" * 101))
        assert item.is_reference is True
        assert item.reference_reason is ReferenceReason.LARGER_THAN_BUDGET

    def test_a_body_that_would_have_fitted_but_the_room_was_spent(self) -> None:
        first, second = fit(make_item("x" * 80, priority=1), make_item("y" * 30, priority=2))
        assert first.reference_reason is None
        assert second.is_reference is True
        assert second.reference_reason is ReferenceReason.BUDGET_SPENT

    def test_the_full_document_limit(self) -> None:
        first, second = fit(
            make_item("x" * 10, priority=1), make_item("y" * 10, priority=2), max_full_documents=1
        )
        assert first.reference_reason is None
        assert second.reference_reason is ReferenceReason.DOCUMENT_LIMIT

    def test_larger_than_budget_wins_over_the_other_two(self) -> None:
        # It is the one a person has to act on, so it is never hidden behind a
        # reason that says the pack was merely full.
        _, second = fit(
            make_item("x" * 10, priority=1), make_item("y" * 101, priority=2), max_full_documents=1
        )
        assert second.reference_reason is ReferenceReason.LARGER_THAN_BUDGET

    def test_an_item_that_never_had_a_body_is_not_a_substitution(self) -> None:
        (item,) = fit(make_item(None))
        assert item.is_reference is True
        assert item.reference_reason is None

    def test_the_vocabulary_is_closed(self) -> None:
        assert {reason.value for reason in ReferenceReason} == {
            "larger_than_budget",
            "budget_spent",
            "document_limit",
        }
