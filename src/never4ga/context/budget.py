"""Budget application (core/07 section 10).

"Do not ask an LLM to decide whether a 30,000-token startup pack is too large."

When a pack exceeds its budget, low-priority bodies are replaced by *references*
rather than deleted: the agent still learns the document exists and can fetch
it, which is the point of progressive context.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from never4ga.domain.context import (
    BudgetUsage,
    ContextBudget,
    ContextItem,
    PackCategory,
    ReferenceReason,
    estimate_tokens,
)

__all__ = [
    "DEEP_CATEGORY_LIMITS",
    "REQUIRED_READING_CEILING",
    "STARTUP_CATEGORY_LIMITS",
    "TRANSPORT_LIMIT",
    "apply_budget",
    "deep_budget",
    "startup_budget",
]

#: How many items of each kind a startup pack leads with.
#:
#: Without per-category caps one lane can take the whole pack: a workspace with
#: many standards would crowd out every decision and log. core/07 section 10
#: wants the pack bounded, and the `PackCategory` docstring wants it bounded
#: *per kind*, because "a pack that has lost a whole category is worse than one
#: that is merely shorter".
#:
#: The numbers are a starting position, chosen empirically rather than derived.
#: A caller may override any of them. Workspace and parent items are
#: deliberately uncapped: what the workspace *is* is not optional.
STARTUP_CATEGORY_LIMITS: Final[Mapping[str, int]] = MappingProxyType(
    {
        PackCategory.CONTEXT.value: 8,
        # Governs only standards that are *not* binding -- in practice the
        # archived ones, which no longer apply. Every live standard is
        # binding and bypasses this entirely: a rule that is obeyed only
        # while the first six last is not a rule.
        PackCategory.STANDARD.value: 6,
        PackCategory.GOAL.value: 6,
        # Governs only decisions that are *not* in force -- proposed,
        # superseded, rejected. An accepted decision is binding and bypasses
        # this entirely, for the reason a live standard does: it is a ruling
        # that applies whatever the session is doing. See
        # `structural.py::_decision_items`.
        PackCategory.DECISION.value: 8,
        # The current milestone plan is what a session most often needs, and a
        # workspace rarely has many in play at once.
        PackCategory.PLAN.value: 6,
        PackCategory.TASK.value: 10,
        PackCategory.ACTIVITY.value: 3,
        # Empty unless core/04 section 16's `--task` named one. Short on
        # purpose: a task makes startup task-aware, not task-led, and anything
        # more than a hint belongs to `context focus`.
        PackCategory.RETRIEVED.value: 5,
    }
)


#: What a deep pack leads with. Chosen empirically, like the startup limits.
#:
#: Deep is the mode for asking about a lineage rather than a session, so the
#: numbers are set to admit a real one whole and let the *character* ceiling do
#: the trimming. That distinction is the reason they are this generous: an item
#: past a category cap is dropped and its existence is lost, while an item past
#: the character budget becomes a reference and the reader still learns it is
#: there. core/04 section 19 asks for "summary + references" over whole
#: documents, so at this depth the cap should not be the thing that binds.
#: Tighter caps drop whole parts of a workspace's decision record outright.
#:
#: `RETRIEVED` is the only category that is empty when no terms were given:
#: `context startup --depth deep` is a wider startup pack, not a search with
#: nothing to search for.
DEEP_CATEGORY_LIMITS: Final[Mapping[str, int]] = MappingProxyType(
    {
        PackCategory.CONTEXT.value: 20,
        PackCategory.STANDARD.value: 20,
        PackCategory.GOAL.value: 15,
        PackCategory.DECISION.value: 30,
        PackCategory.PLAN.value: 15,
        PackCategory.TASK.value: 40,
        PackCategory.ACTIVITY.value: 15,
        PackCategory.RETRIEVED.value: 30,
    }
)


def deep_budget(
    *,
    max_items: int = 120,
    max_characters: int = 120_000,
    max_full_documents: int | None = None,
    category_limits: Mapping[str, int] | None = None,
) -> ContextBudget:
    """The budget a deep pack uses when the caller states no preference.

    Deep is bounded, and being bounded is the point: core/07 section 10 admits
    no depth at which a pack stops having a ceiling. What deep raises is the
    ceiling, never the rule.
    """
    return ContextBudget(
        max_items=max_items,
        max_characters=max_characters,
        max_full_documents=max_full_documents,
        category_limits=(DEEP_CATEGORY_LIMITS if category_limits is None else category_limits),
    )


#: The most a single tool response should hold (core/07 section 10). Agent
#: clients truncate shell output past about 30 KB, showing a short preview and
#: saving the rest to a file the agent must choose to open, and MCP responses
#: carry a similar cap. A larger pack is not read by being larger; required
#: reading that does not fit is read from its file.
TRANSPORT_LIMIT: Final = 24_000

#: Where a workspace's required reading stops being a trade worth making
#: unexamined: about 50k tokens, low enough that the warning comes with room to
#: act (core/07 section 10). Reported, never enforced by cutting.
REQUIRED_READING_CEILING: Final = 200_000

#: What one index line costs beyond its title: category, reason, and a
#: reference's id and reason. An empirical average, rounded up;
#: `test_required_reading.py` holds a real response under the limit.
INDEX_LINE_ALLOWANCE: Final = 110

#: What a required item's `[required, read in full: <path>, <size>]` mark adds
#: beyond its vault path: the vault root in front of it, and the size.
REQUIRED_MARK_ALLOWANCE: Final = 90

#: What a description line adds beyond its text: its indentation and newline.
DESCRIPTION_LINE_ALLOWANCE: Final = 16

#: The header, the signals and the framing around the bodies.
RESPONSE_FRAMING_ALLOWANCE: Final = 5_000


def startup_budget(
    *,
    max_items: int = 40,
    # Two thirds of the transport limit for bodies; the rest is the index,
    # the signals and the framing around them (core/07 section 10).
    max_characters: int = TRANSPORT_LIMIT * 2 // 3,
    response_limit: int | None = TRANSPORT_LIMIT,
    max_full_documents: int | None = None,
    category_limits: Mapping[str, int] | None = None,
) -> ContextBudget:
    """The budget a startup pack uses when the caller states no preference.

    Defined once so the CLI, the HTTP API and MCP cannot answer the same
    question differently (details/api-cli-mcp-contract.md section 1).
    """
    return ContextBudget(
        max_items=max_items,
        max_characters=max_characters,
        max_full_documents=max_full_documents,
        category_limits=(STARTUP_CATEGORY_LIMITS if category_limits is None else category_limits),
        response_limit=response_limit,
    )


def _body_room(admitted: list[ContextItem], budget: ContextBudget) -> int:
    """How many characters of bodies fit: the budget, or what the index leaves.

    core/07 section 10 keeps a response under its transport limit, and the
    index is part of the response: a large workspace's index alone can take
    most of it. So the index is estimated first and bodies get the rest.
    Required reading that finds no room is read from its file, never cut.
    """
    if budget.response_limit is None:
        return budget.max_characters
    index = sum(
        len(item.title)
        + INDEX_LINE_ALLOWANCE
        + (len(str(item.path)) + REQUIRED_MARK_ALLOWANCE if item.required else 0)
        + (len(item.description) + DESCRIPTION_LINE_ALLOWANCE if item.description else 0)
        for item in admitted
    )
    left = budget.response_limit - RESPONSE_FRAMING_ALLOWANCE - index
    return max(0, min(budget.max_characters, left))


def _order(item: ContextItem) -> tuple[int, str]:
    """Priority, then owner: the concept's id, or a foreign note's path."""
    owner = str(item.concept_id) if item.concept_id is not None else f"path:{item.path}"
    return (item.priority, owner)


def apply_budget(
    items: Iterable[ContextItem],
    budget: ContextBudget,
) -> tuple[tuple[ContextItem, ...], BudgetUsage]:
    """Order by priority, then fit the pack to its budget.

    Ordering is fully deterministic -- priority first, then identity -- so the
    same inputs always produce the same pack.
    """
    ordered = sorted(items, key=_order)

    # Per-category caps run first, and on the ordered list, so what survives a
    # cap is the highest-priority item of that kind rather than whichever the
    # index happened to return first.
    within_category: list[ContextItem] = []
    seen: Counter[str] = Counter()
    for item in ordered:
        # A binding rule is not one of its category's candidates -- it applies
        # whatever the session is doing, so a cap has no standing to drop it.
        # Without this, "no credential enters a tracked file" competes with a
        # brand palette for the same six slots and can lose.
        if item.binding:
            within_category.append(item)
            continue
        # Required reading is never dropped either, but it is still
        # one of its category's items: a cap of one log means the handoff alone,
        # not the handoff plus the next log along.
        if item.required:
            seen[item.category] += 1
            within_category.append(item)
            continue
        limit = budget.limit_for(item.category)
        if limit is not None and seen[item.category] >= limit:
            continue
        seen[item.category] += 1
        within_category.append(item)

    # Binding items are admitted whole and do not spend the item ceiling. That
    # ceiling exists so one lane cannot crowd out another; a mandatory rule is
    # not a lane competing for room, and charging it against the ceiling would
    # reintroduce the crowding-out through the back door -- thirty standards
    # would leave ten slots for every decision and log the session needs.
    binding = [item for item in within_category if item.binding or item.required]
    optional = [item for item in within_category if not (item.binding or item.required)]
    admitted = sorted(
        [*binding, *optional[: budget.max_items]],
        key=_order,
    )
    dropped = len(ordered) - len(admitted)

    # Bodies are admitted fresh first, then stale, and the pack keeps its
    # order either way. core/02 section 15.2 wants stale content downgraded
    # during assembly and never removed: a stale body takes whatever room the
    # fresh ones left, and one that finds none stays as a reference -- still
    # present, still flagged, only outranked for space.
    #
    # A body that is not carried records why (core/07 section 10). Larger
    # than the whole budget is checked first because it is the one a person
    # must act on, and it must not hide behind a reason that says the pack
    # was merely full.
    with_body: set[int] = set()
    refused: dict[int, ReferenceReason] = {}
    characters = 0
    full_documents = 0
    #
    # Required reading goes first: what does not fit inline stays required and
    # is read from its file, so it is never cut, but inline room is spent on it
    # before anything else. Binding rules come next, before anything optional
    # (core/07 section 10): a plan must not take the room a standard needed.
    order = sorted(
        range(len(admitted)),
        key=lambda i: (not admitted[i].required, not admitted[i].binding, admitted[i].is_stale),
    )
    room = _body_room(admitted, budget)
    for position in order:
        body = admitted[position].body
        if body is None:
            continue
        if len(body) > room:
            refused[position] = ReferenceReason.LARGER_THAN_BUDGET
        elif full_documents >= budget.full_document_limit:
            refused[position] = ReferenceReason.DOCUMENT_LIMIT
        elif characters + len(body) > room:
            refused[position] = ReferenceReason.BUDGET_SPENT
        else:
            characters += len(body)
            full_documents += 1
            with_body.add(position)
    kept = [
        item if position in with_body else item.as_reference(refused.get(position))
        for position, item in enumerate(admitted)
    ]

    usage = BudgetUsage(
        items=len(kept),
        characters=characters,
        estimated_tokens=estimate_tokens("x" * characters, budget.chars_per_token),
        full_documents=full_documents,
        dropped_items=dropped,
        required_characters=sum(item.size or 0 for item in kept if item.required),
    )
    return tuple(kept), usage
