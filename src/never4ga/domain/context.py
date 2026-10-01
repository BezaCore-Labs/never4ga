"""Context Pack types and budgets.

core/07 section 12 requires every item to say how it arrived; section 15
requires startup to be bounded and to need no Never4gA-owned model call; section
10 makes budgeting arithmetic rather than a judgement call. All three are
properties of these types rather than of any particular assembler.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.provenance import AcquisitionReason
from never4ga.domain.scope import ResolvedScope, ScopeRequest
from never4ga.domain.signals import ContextSignal
from never4ga.errors import ContextBudgetError

__all__ = [
    "DEFAULT_CHARS_PER_TOKEN",
    "BudgetUsage",
    "ContextBudget",
    "ContextDepth",
    "ContextItem",
    "ContextPack",
    "ContextRequest",
    "PackCategory",
    "ReferenceReason",
    "estimate_tokens",
    "terms_from_task",
]

#: A deterministic characters-per-token approximation. core/07 section 10 allows
#: a real tokenizer "when available"; none is a Never4gA dependency, and the
#: estimate must never require one.
DEFAULT_CHARS_PER_TOKEN = 4


class ContextDepth(enum.StrEnum):
    """Progressive context (core/07 section 1)."""

    STARTUP = "startup"
    FOCUSED = "focused"
    DEEP = "deep"


class ReferenceReason(enum.StrEnum):
    """Why the budget carried an item as a reference (core/07 section 10).

    The causes need different responses. A document too large for any pack is
    something a person has to fix; one that arrived after the room was spent
    needs nothing.
    """

    #: The body alone is larger than the pack's character budget. No ordering
    #: could have carried it.
    LARGER_THAN_BUDGET = "larger_than_budget"
    #: It would have fitted, but higher-priority bodies used the room first.
    BUDGET_SPENT = "budget_spent"
    #: The pack's limit on full documents was reached.
    DOCUMENT_LIMIT = "document_limit"


class PackCategory(enum.StrEnum):
    """What kind of thing an item is, for budgeting and for reading.

    core/07 leaves the pack's contents open. The category exists so a budget
    can be expressed per kind rather than as one undifferentiated ceiling: a
    character limit alone lets a single long manifest crowd out every decision
    and every open task, and a pack that has lost a whole category is worse
    than one that is merely shorter.
    """

    WORKSPACE = "workspace"
    PARENT = "parent"
    #: The workspace's own orientation: core/03 section 5.1's "durable
    #: orientation/current-state material", so a pack says what state the
    #: work is in and not only what was decided.
    CONTEXT = "context"
    STANDARD = "standard"
    #: What the workspace is trying to achieve. core/07 section 9 names "active
    #: goal titles" in the mechanical summary.
    GOAL = "goal"
    DECISION = "decision"
    #: How it intends to get there. A lane of its own, so a current plan does
    #: not depend on lexical retrieval happening to find it.
    PLAN = "plan"
    TASK = "task"
    ACTIVITY = "activity"

    #: Anything retrieval turned up that is not one of the kinds above -- a
    #: knowledge note, an entity, a document type Never4gA does not model. It is
    #: a real category rather than a fallback to "workspace", because labelling a
    #: note as a workspace in a pack is a small lie an agent has no way to check.
    RETRIEVED = "retrieved"


def estimate_tokens(text: str, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN) -> int:
    """A deterministic token estimate that requires no model."""
    return -(-len(text) // chars_per_token)


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Hard limits on an assembled Context Pack.

    Deliberately holds no model, provider or tokenizer configuration: a budget
    is meaningful without knowing which agent will read the pack.
    """

    max_items: int
    max_characters: int
    max_full_documents: int | None = None
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN
    category_limits: Mapping[str, int] = field(default_factory=dict)
    #: The most the whole response should hold, index included
    #: (core/07 section 10). Bodies get what the index leaves; ``None`` means
    #: only the budget binds.
    response_limit: int | None = None

    def __post_init__(self) -> None:
        if self.max_items < 1:
            raise ContextBudgetError(f"max_items must be at least 1, got {self.max_items}")
        if self.max_characters < 1:
            raise ContextBudgetError(
                f"max_characters must be at least 1, got {self.max_characters}"
            )
        if self.max_full_documents is not None and self.max_full_documents < 0:
            raise ContextBudgetError(
                f"max_full_documents cannot be negative, got {self.max_full_documents}"
            )
        if self.response_limit is not None and self.response_limit < 1:
            raise ContextBudgetError(
                f"response_limit must be at least 1, got {self.response_limit}"
            )
        if self.chars_per_token < 1:
            raise ContextBudgetError(
                f"chars_per_token must be at least 1, got {self.chars_per_token}"
            )
        for category, limit in self.category_limits.items():
            if limit < 0:
                raise ContextBudgetError(
                    f"the limit for {category!r} cannot be negative, got {limit}"
                )
        object.__setattr__(self, "category_limits", MappingProxyType(dict(self.category_limits)))

    @property
    def full_document_limit(self) -> int:
        return self.max_full_documents if self.max_full_documents is not None else self.max_items

    def limit_for(self, category: str) -> int | None:
        """How many items of this kind may be admitted. None means uncapped."""
        return self.category_limits.get(category)


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One document in a Context Pack.

    ``priority`` is ascending: 0 is the most important. An item whose body was
    dropped for budget remains present as a *reference*, so the agent knows it
    exists and can request it.

    ``concept_id`` is ``None`` for a note in foreign material, which retrieval
    reaches by its ``path`` and which has no identity (core/01 section 1).
    """

    concept_id: ConceptId | None
    path: VaultPath
    title: str
    priority: int
    reason: AcquisitionReason
    body: str | None = None
    is_reference: bool = False
    category: str = PackCategory.WORKSPACE
    #: A rule that binds regardless of what the session is doing, and so must
    #: appear in every pack. Exempt from category caps and the item ceiling,
    #: and placed after required reading and before anything optional. Not
    #: exempt from the character budget unless it is also required: past it,
    #: it degrades to a reference and the reader still learns it exists.
    binding: bool = False
    #: The `stale_after` this document set for itself, once it has passed, and
    #: ``None`` otherwise (core/02 section 15). The instant rather than a flag,
    #: so a reader can see how long ago the claim lapsed. Section 15.2 keeps a
    #: stale item in the pack, visibly flagged; the budget downgrades its body.
    stale_since: datetime | None = None
    #: Why the budget replaced this item's body with a reference
    #: (core/07 section 10). ``None`` for a carried body, and for an item that
    #: never had one to carry.
    reference_reason: ReferenceReason | None = None
    #: Required reading (core/07 section 10): never budgeted away, and read in
    #: full -- inline when it fits, from its file when it does not.
    required: bool = False
    #: The body's length in characters, kept when the body is not carried, so
    #: a reader knows what reading it costs and the pack can total it.
    size: int | None = None
    #: What the document says it governs. A standard listed without its body
    #: carries it, so the reader can tell when to read it (core/07 section 10).
    description: str | None = None

    @property
    def is_stale(self) -> bool:
        return self.stale_since is not None

    def as_reference(self, reason: ReferenceReason | None = None) -> ContextItem:
        if self.is_reference and self.body is None and reason is None:
            return self
        size = self.size if self.size is not None or self.body is None else len(self.body)
        return replace(self, body=None, is_reference=True, reference_reason=reason, size=size)


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """What an assembled pack actually cost."""

    items: int = 0
    characters: int = 0
    estimated_tokens: int = 0
    full_documents: int = 0
    dropped_items: int = 0
    #: The whole of the pack's required reading, inline or not.
    required_characters: int = 0


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """A public context request.

    There is no prompt, model, temperature or provider field, and there will not
    be one: the request describes *what context is wanted*, not how some model
    should be asked for it (core/07 section 1).
    Retrieval terms are structured for the same reason as
    :class:`~never4ga.ports.text_index.TextQuery`.
    """

    scope: ScopeRequest
    depth: ContextDepth
    budget: ContextBudget
    terms: tuple[str, ...] = ()
    #: Contiguous word sequences, as :class:`~never4ga.ports.text_index.TextQuery`
    #: carries them. No interface sends one directly: `ContextService` finds them
    #: in ``terms`` the way `search` finds them in a query.
    phrases: tuple[str, ...] = ()
    exact_identifiers: tuple[str, ...] = ()
    #: Who is writing, for the session a startup opens. `None` means the
    #: caller did not say, and the service records the owner -- the same
    #: default `concept create` uses. Distinct from `client`: one names the
    #: tool, this names the tool *and its model*, which is what core/02
    #: section 5.2 wants in `generated.by`.
    actor: str | None = None
    #: Which agent client asked (core/04 section 16). A label a user chose from
    #: a list, carried so a pack can say who it was assembled for.
    client: str | None = None
    #: That a task description was given -- never what it said. core/04 section
    #: 16: the full raw user prompt SHOULD NOT be persisted merely because it
    #: was used for retrieval, so the task reaches ``terms`` and stops there.
    task_given: bool = False
    #: One external work item to centre the pack on. A provider-side
    #: reference, deliberately not spelled `ticket`: core/03 calls them work
    #: items, and one tracker's vocabulary must not reach the domain. It says
    #: *what context is wanted* -- this piece of work -- which is why it
    #: belongs here and a prompt does not.
    work_item: str | None = None
    #: Ask the source rather than whatever was cached. A statement about the
    #: context wanted, not about how to fetch it: "current" is a property of an
    #: answer.
    refresh: bool = False


@dataclass(frozen=True, slots=True)
class ContextPack:
    """The assembled result."""

    scope: ResolvedScope
    items: tuple[ContextItem, ...] = ()
    signals: tuple[ContextSignal, ...] = ()
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    #: Names of any LLM enrichment stages that ran. Empty means none did, and
    #: startup must always be empty (core/07 sections 8 and 15).
    llm_stages: tuple[str, ...] = ()
    #: Optional providers that failed and were skipped (core/05 section 19).
    degraded_providers: tuple[str, ...] = ()
    #: Who asked, and whether they named a task. Both come straight from the
    #: request; the task's words do not.
    client: str | None = None
    task_given: bool = False
    #: The session this pack opened, when it opened one. Only a startup pack
    #: does: core/04 section 34 runs startup once per session and focused
    #: retrieval many times within it, so minting on every retrieval would make
    #: each one look like a new session.
    session: SessionId | None = None

    @property
    def used_llm(self) -> bool:
        return bool(self.llm_stages)


#: What separates one word of a task description from the next. Anything that is
#: not a letter, a digit or an underscore: a task is prose, and prose carries
#: punctuation that FTS has no use for.
_WORD_BOUNDARY: Final = re.compile(r"[^\w]+", re.UNICODE)


def terms_from_task(task: str) -> tuple[str, ...]:
    """Retrieval terms from a short task description (core/04 section 16).

    Deliberately unclever. The terms are OR-ed and ranked by BM25, and a
    stopword list would be a vocabulary decision made in the wrong place.
    Single characters go, because they match everything and mean nothing.

    Common words still cost something: a long document matches many of them
    many times and can outrank the real answer. Dropping terms above a
    document-frequency threshold looks like the fix and is not, because every
    threshold that removes that noise also lowers ranking quality. The vector
    lane answers it instead, by adding meaning rather than removing words.

    The description itself goes no further than this function. What survives is
    a list of words to search for, which is the only part retrieval needs.
    """
    words = (word for word in _WORD_BOUNDARY.split(task) if len(word) > 1)
    seen: dict[str, None] = {}
    for word in words:
        seen.setdefault(word, None)
    return tuple(seen)
