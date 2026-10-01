"""Mechanical context budgeting (core/07 section 10).

Budgeting needs no LLM: it counts characters, items and full documents.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from never4ga.context.budget import apply_budget
from never4ga.domain.context import ContextBudget, ContextItem, estimate_tokens
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.errors import ContextBudgetError


def make_item(body: str, priority: int = 10, title: str = "Thing") -> ContextItem:
    return ContextItem(
        concept_id=ConceptId.new(),
        path=VaultPath.parse("30_Knowledge/Notes/thing.md"),
        title=title,
        priority=priority,
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
        body=body,
    )


class TestContextBudget:
    def test_rejects_incoherent_budgets(self) -> None:
        incoherent: tuple[dict[str, Any], ...] = (
            {"max_items": 0},
            {"max_characters": 0},
            {"max_full_documents": -1},
            {"chars_per_token": 0},
            {"category_limits": {"decision": -1}},
        )
        for kwargs in incoherent:
            with pytest.raises(ContextBudgetError):
                ContextBudget(**{"max_items": 5, "max_characters": 100, **kwargs})

    def test_category_limits_cannot_be_mutated_after_construction(self) -> None:
        budget = ContextBudget(max_items=5, max_characters=100, category_limits={"decision": 2})
        with pytest.raises(TypeError):
            budget.category_limits["decision"] = 9  # type: ignore[index]

    def test_an_uncapped_category_reports_no_limit(self) -> None:
        budget = ContextBudget(max_items=5, max_characters=100, category_limits={"decision": 2})
        assert budget.limit_for("decision") == 2
        assert budget.limit_for("activity") is None

    def test_token_estimation_is_deterministic_and_needs_no_model(self) -> None:
        budget = ContextBudget(max_items=5, max_characters=100)
        assert estimate_tokens("a" * 400, budget.chars_per_token) == 100
        assert estimate_tokens("a" * 400, budget.chars_per_token) == 100

    def test_the_budget_holds_no_model_configuration(self) -> None:
        budget = ContextBudget(max_items=5, max_characters=100)
        for forbidden in ("model", "provider", "tokenizer", "prompt", "api_key"):
            assert not hasattr(budget, forbidden)


class TestApplyBudget:
    def test_an_under_budget_pack_is_untouched(self) -> None:
        items = [make_item("short", priority=1)]
        kept, usage = apply_budget(items, ContextBudget(max_items=5, max_characters=1000))
        assert kept[0].body == "short"
        assert usage.items == 1
        assert usage.full_documents == 1

    def test_priority_order_is_applied(self) -> None:
        low = make_item("a", priority=99, title="Low")
        high = make_item("b", priority=1, title="High")
        kept, _ = apply_budget([low, high], ContextBudget(max_items=5, max_characters=1000))
        assert [item.title for item in kept] == ["High", "Low"]

    def test_over_budget_bodies_become_references_not_deletions(self) -> None:
        # core/07 section 10: apply priority ordering and truncation or
        # reference substitution -- the item still tells the agent it exists.
        keep = make_item("x" * 50, priority=1, title="Keep")
        drop_body = make_item("y" * 500, priority=2, title="Reference")
        kept, usage = apply_budget(
            [keep, drop_body], ContextBudget(max_items=5, max_characters=100)
        )
        assert [item.title for item in kept] == ["Keep", "Reference"]
        assert kept[0].body == "x" * 50
        assert kept[1].body is None
        assert kept[1].is_reference
        assert usage.full_documents == 1

    def test_full_document_count_is_capped(self) -> None:
        items = [make_item("body", priority=i) for i in range(4)]
        kept, usage = apply_budget(
            items, ContextBudget(max_items=10, max_characters=10_000, max_full_documents=2)
        )
        assert usage.full_documents == 2
        assert [item.is_reference for item in kept] == [False, False, True, True]

    def test_item_count_is_capped_and_the_overflow_is_reported(self) -> None:
        items = [make_item("body", priority=i) for i in range(5)]
        kept, usage = apply_budget(items, ContextBudget(max_items=2, max_characters=10_000))
        assert len(kept) == 2
        assert usage.items == 2
        assert usage.dropped_items == 3

    def test_usage_reports_characters_and_estimated_tokens(self) -> None:
        _, usage = apply_budget(
            [make_item("a" * 40)], ContextBudget(max_items=5, max_characters=1000)
        )
        assert usage.characters == 40
        assert usage.estimated_tokens == 10

    def test_a_reference_costs_nothing_but_its_title(self) -> None:
        kept, usage = apply_budget(
            [make_item("y" * 500)], ContextBudget(max_items=5, max_characters=10)
        )
        assert kept[0].is_reference
        assert usage.characters == 0

    def test_an_empty_pack_is_valid(self) -> None:
        kept, usage = apply_budget([], ContextBudget(max_items=5, max_characters=100))
        assert kept == ()
        assert usage.items == 0

    def test_budgeting_is_deterministic(self) -> None:
        items = [make_item("body" * 20, priority=i) for i in range(5)]
        budget = ContextBudget(max_items=3, max_characters=100)
        assert apply_budget(items, budget) == apply_budget(items, budget)

    def test_every_kept_item_still_carries_its_reason(self) -> None:
        kept, _ = apply_budget(
            [make_item("y" * 500)], ContextBudget(max_items=5, max_characters=10)
        )
        assert kept[0].reason.code == ReasonCode.WORKSPACE_REQUIRED


class TestABindingRuleIsNeverDropped:
    """A rule that binds every project must arrive in every pack.

    A standard in a workspace's `Brand/` folder applies to brand work. A
    standard with no workspace binds the whole vault, such as "no credential
    enters a tracked file". The category cap on standards must not drop the
    second to make room for the first.

    A binding item is exempt from the category cap and the item ceiling. It is
    not exempt from the character budget: past that it becomes a reference,
    which still tells the reader the rule exists.
    """

    def binding(self, body: str, priority: int = 10) -> ContextItem:
        return replace(make_item(body, priority=priority), category="standard", binding=True)

    def scoped(self, body: str, priority: int = 10) -> ContextItem:
        return replace(make_item(body, priority=priority), category="standard")

    def test_a_binding_rule_survives_a_category_cap_that_excludes_it(self) -> None:
        items = [self.scoped("scoped", priority=1) for _ in range(6)]
        items.append(self.binding("secrets never enter a tracked file", priority=99))

        kept, usage = apply_budget(
            items,
            ContextBudget(max_items=40, max_characters=10_000, category_limits={"standard": 6}),
        )

        assert any(item.binding for item in kept), "the binding rule was dropped"
        assert usage.dropped_items == 0

    def test_a_scoped_standard_is_still_capped(self) -> None:
        items = [self.scoped(f"scoped {n}", priority=n) for n in range(10)]

        kept, usage = apply_budget(
            items,
            ContextBudget(max_items=40, max_characters=10_000, category_limits={"standard": 6}),
        )

        assert len(kept) == 6
        assert usage.dropped_items == 4

    def test_a_binding_body_is_placed_before_an_optional_one(self) -> None:
        # core/07 section 10: after required reading, binding bodies come
        # before anything optional, whatever their priority.
        plan = replace(make_item("p" * 200, priority=1, title="Plan"), category="plan")
        rule = self.binding("r" * 200, priority=2)

        kept, _ = apply_budget([plan, rule], ContextBudget(max_items=40, max_characters=250))

        by_category = {item.category: item for item in kept}
        assert not by_category["standard"].is_reference
        assert by_category["plan"].is_reference

    def test_a_required_body_is_still_placed_before_a_binding_one(self) -> None:
        required = replace(make_item("q" * 200, priority=5), category="context", required=True)
        rule = self.binding("r" * 200, priority=1)

        kept, _ = apply_budget([required, rule], ContextBudget(max_items=40, max_characters=250))

        by_category = {item.category: item for item in kept}
        assert not by_category["context"].is_reference
        assert by_category["standard"].is_reference

    def test_a_binding_rule_past_the_character_budget_becomes_a_reference(self) -> None:
        # Degraded, never deleted: the reader still learns the rule exists.
        items = [self.scoped("x" * 200, priority=1), self.binding("y" * 300, priority=2)]

        kept, _ = apply_budget(
            items,
            ContextBudget(max_items=40, max_characters=250, category_limits={"standard": 6}),
        )

        binding = [item for item in kept if item.binding]
        assert binding and binding[0].is_reference
        assert binding[0].body is None
