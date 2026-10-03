"""The structural pass: what a workspace *is*, read from metadata alone.

core/07 Stage B. No search, no model, no body scanning -- just the projected
frontmatter of documents whose type and workspace say they belong. Startup is
this pass and nothing else; deep is this pass reaching further, plus retrieval.

How far it reaches is a :class:`Reach`, which is the whole difference between
the two depths on this side (core/04 section 18: deep "may traverse larger
parent context"). Everything else about the pass -- the priority bands, the
orderings, the acquisition reasons -- is identical at both, because a decision
does not become a different kind of thing because it was asked for differently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from never4ga.context.foreign import foreign_item
from never4ga.context.freshness import Clock, stale_since, utc_now
from never4ga.context.fusion import FusedCandidate
from never4ga.context.required import StandardFacts, excepted_targets, required_standards
from never4ga.domain.context import ContextItem, PackCategory
from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope
from never4ga.layout import VaultRoot
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.metadata_index import MetadataIndex, MetadataQuery, MetadataRecord

__all__ = [
    "DEEP_REACH",
    "STARTUP_REACH",
    "VAULT_WIDE_PACK_TYPES",
    "WORKSPACE_SCOPED_PACK_TYPES",
    "Reach",
    "StructuralContext",
]

#: Priority bands, ascending: 0 is read first and survives the tightest budget.
#: The workspace and its parents occupy 0 upward, one per generation, so the
#: bands start well clear of MAX_PARENT_DEPTH.
CONTEXT_BAND: Final = 50
STANDARD_BAND: Final = 100

#: How far a within-band rank may move an item. Ranks are clamped to it so a
#: workspace with a hundred standards cannot push one into the decision band.
#: Past the clamp, items tie and break on identity, which is still deterministic.
BAND_SPAN: Final = 100
#: Goals before decisions, decisions before plans, in the order core/07 section
#: 9 names them: what is being aimed at, what has been settled, then how it is
#: being done.
GOAL_BAND: Final = 200
DECISION_BAND: Final = 300
PLAN_BAND: Final = 400
TASK_BAND: Final = 500
ACTIVITY_BAND: Final = 600

#: Where retrieved items sit: after everything structure alone can say. A
#: document found by searching is not more important than what the workspace is.
RETRIEVED_BAND: Final = 700

#: The one lifecycle that means "in force". It is what makes a decision binding.
_ACCEPTED: Final = "accepted"

#: core/02 section 21.11's decision lifecycle, ranked by authority. An
#: unrecognised value sorts last rather than being dropped: core/00 #15 requires
#: unknown vocabulary to survive, and startup is not the place to adjudicate it.
_DECISION_RANK: Final = {_ACCEPTED: 0, "proposed": 1, "superseded": 2, "rejected": 3}

#: core/02 section 21.8: a goal still being pursued. An achieved or abandoned
#: one is history rather than context.
_OPEN_GOAL_LIFECYCLES: Final = frozenset({"proposed", "active"})

#: core/02 section 21.9: a plan still in play. `draft` counts -- a plan being
#: written is exactly what a session may be there to continue.
_OPEN_PLAN_LIFECYCLES: Final = frozenset({"draft", "active"})

#: core/02 section 21.27: the phase a session is most likely there to continue.
_IN_PROGRESS: Final = "in_progress"

#: core/02 section 21.10. Work that is finished is not work in flight.
_CLOSED_TASK_LIFECYCLES: Final = frozenset({"done", "cancelled"})

#: The types a structural lane below carries, and what each lane needs in order
#: to see one. These name the lanes rather than describing them, so **they move
#: when a lane moves** -- adding `_foo_items` without adding its type here
#: leaves something claiming not to resurface when it does.
#:
#: The distinction they draw is the one `inbox resolve` asks about: a
#: workspace-scoped type arrives unasked only when its lane can match it, so one
#: with no workspace is carried by nothing. A type absent from both sets is not
#: broken -- `knowledge`, `resource` and `entity` are reference material, reached
#: by searching for them, and were never promised to arrive on their own.
WORKSPACE_SCOPED_PACK_TYPES: Final = frozenset(
    {"context", "decision", "goal", "plan", "walkthrough", "task", "activity_log"}
)

#: Carried without a workspace filter. A standard binds the whole lineage, so
#: `_standard_items` queries every one in the vault; a workspace manifest is what
#: `_workspace_items` resolves the scope *to*.
VAULT_WIDE_PACK_TYPES: Final = frozenset({"standard", "workspace"})


@dataclass(frozen=True, slots=True)
class Reach:
    """How far a structural pass reaches out from the resolved workspace.

    ``parent_sections`` admits a parent's own context, decisions, open work and
    activity, not just its manifest. Startup does not: a parent's current state
    is the parent's, and a session opening in a child needs to know the parent
    exists far more than it needs the parent's task list. Deep does, because
    deep is the mode for asking about the whole lineage.

    Standards are absent from this dial deliberately. They already reach the
    parent chain at every depth, because a rule that binds an organization binds
    its projects whether or not anybody asked deeply.
    """

    parent_sections: bool = False


#: Startup: this workspace's own state, and the parent chain by name.
STARTUP_REACH: Final = Reach()

#: Deep: the lineage's state as well (core/04 section 18, "larger parent context").
DEEP_REACH: Final = Reach(parent_sections=True)


def is_detached(record: MetadataRecord) -> bool:
    """Whether a record lives in `90_Archive/` and is therefore out of scope.

    `core/01` calls the archive "intentionally detached historical material",
    and detached has to mean detached: archived material is excluded from every
    structural lane, not ranked low within one. Something is archived precisely
    because it stopped applying, so its presence in a startup pack is noise that
    reads exactly like signal: a retired standard is still shaped like a rule.

    Archived material is reachable by opening it and by following a link to it.
    Search does not reach it either (core/07 Stage B);
    `services/search.py::_is_archived` is the other half of the same rule. The
    remedy for history worth retrieving is a filing discipline, not a retrieval
    feature.
    """
    return record.path.root == VaultRoot.ARCHIVE


def standards_by_breadth(records: list[MetadataRecord]) -> list[MetadataRecord]:
    """Standards ordered so a cap takes a spread, not one folder.

    Path order alone is not a ranking, and once a cap exists it becomes one:
    the first six standards by path can all come from one folder, and the pack
    then says nothing about any other area.

    So take one per directory, then a second from each, and so on. A vault-wide
    standard leads, because it binds everything and is the one a session can
    least afford not to know about. Within a directory, path order still decides,
    so the result is fully deterministic.
    """
    grouped: dict[str, list[MetadataRecord]] = {}
    for record in sorted(records, key=lambda r: str(r.path)):
        # A vault-wide Standard has no workspace, and gets its own leading group.
        folder = "" if record.workspace_id is None else "/".join(record.path.segments[:-1])
        grouped.setdefault(folder, []).append(record)

    ordered: list[MetadataRecord] = []
    folders = sorted(grouped)
    while any(grouped[folder] for folder in folders):
        for folder in folders:
            if grouped[folder]:
                ordered.append(grouped[folder].pop(0))
    return ordered


def _decision_rank(record: MetadataRecord) -> int:
    return _DECISION_RANK.get(record.lifecycle or "", len(_DECISION_RANK))


def _created_at(record: MetadataRecord) -> str:
    """When the decision was recorded, for ordering only.

    ISO 8601 with an offset sorts correctly as a string, and parsing here would
    mean deciding what to do about a malformed one during startup. A record
    without the field sorts oldest, which is where an undated decision belongs
    against dated ones.
    """
    value = record.extra.get("created_at")
    return value if isinstance(value, str) else ""


def _newest_first(records: Sequence[MetadataRecord]) -> list[MetadataRecord]:
    """Newest first, then path. Two stable passes, for the same reason as above."""
    ordered = sorted(records, key=lambda r: str(r.path))
    ordered.sort(key=_created_at, reverse=True)
    return ordered


def _by_authority_then_recency(records: Sequence[MetadataRecord]) -> list[MetadataRecord]:
    """Lifecycle rank ascending, then newest first, then path.

    Three stable passes rather than one key, because the middle term sorts
    *descending* and the others ascend. A single key would need to invert a
    date string to fake it; Python's sort is stable, so applying the weakest
    key first and the strongest last says the same thing and stays readable.
    """
    ordered = sorted(records, key=lambda r: str(r.path))
    ordered.sort(key=_created_at, reverse=True)
    ordered.sort(key=_decision_rank)
    return ordered


def occurred_at(record: MetadataRecord) -> str:
    """When an activity happened, for ordering only.

    ``occurred_at`` is the activity_log field (core/02 section 21.14); a record
    without one falls back to creation. Both are strings in ISO 8601 with an
    offset, which sort correctly without being parsed -- and parsing here would
    mean deciding what to do about a malformed one during startup.
    """
    extra = record.extra
    for field_name in ("occurred_at", "created_at"):
        value = extra.get(field_name)
        if isinstance(value, str) and value:
            return value
    return ""


class StructuralContext:
    """The items a Context Pack can hold before anything is searched.

    ``document_store`` is optional: without it the items carry titles and paths
    only, which is still a useful pack and is what a not-yet-indexed vault can
    offer.
    """

    def __init__(
        self,
        metadata_index: MetadataIndex,
        document_store: DocumentStore | None = None,
        reach: Reach = STARTUP_REACH,
        now: Clock = utc_now,
        mark_required: bool = False,
    ) -> None:
        self._metadata_index = metadata_index
        self._document_store = document_store
        self._reach = reach
        self._now = now
        #: Startup only (core/07 section 10): which items are required reading.
        self._mark_required = mark_required

    def items(self, scope: ResolvedScope) -> list[ContextItem]:
        """Everything structure puts in a pack, before anything is searched.

        Ordered by band so the budget trims from the bottom: what the workspace
        *is*, then the rules that bind it, then what was decided, then what is in
        flight, then what just happened.
        """
        items = [
            *self._workspace_items(scope),
            *self._context_items(scope),
            *self._standard_items(scope),
            *self._goal_items(scope),
            *self._decision_items(scope),
            *self._plan_items(scope),
            *self._walkthrough_items(scope),
            *self._task_items(scope),
            *self._activity_items(scope),
        ]
        if not self._mark_required:
            return items
        handoff = self._handoff(scope)
        standards = required_standards(
            StandardFacts(
                concept_id=str(record.concept_id),
                vault_wide=record.path.root == VaultRoot.SYSTEM,
                required_reading=record.extra.get("required_reading"),
                excepts=excepted_targets(record.extra.get("relations")),
            )
            for record in self._applicable_standards(scope)
        )
        return [
            replace(item, required=True) if _is_required(item, scope, handoff, standards) else item
            for item in items
        ]

    def _handoff(self, scope: ResolvedScope) -> ConceptId | None:
        """The workspace's newest activity log that is not a roll-up.

        A log carrying `covers` summarises a range (core/02 section 21.14); it
        is not what the last session left for the next one, however recent its
        date.
        """
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(types=("activity_log",), workspace_ids=(scope.workspace_id,))
            )
            if not is_detached(record) and not record.extra.get("covers")
        ]
        if not records:
            return None
        return max(records, key=occurred_at).concept_id

    def retrieved(
        self, fused: Sequence[FusedCandidate], already: set[ConceptId]
    ) -> list[ContextItem]:
        """What searching found, after everything structure already said.

        A document the structural pass already carries is not repeated: it is in
        the pack once, with the reason that put it there first. Being found by
        two lanes is not a reason to say it twice.
        """
        items: list[ContextItem] = []
        for rank, candidate in enumerate(fused):
            priority = RETRIEVED_BAND + min(rank, BAND_SPAN - 1)
            if candidate.concept_id is None:
                # Retrieval only: the structural pass never places a pile.
                item = foreign_item(candidate, self._document_store, priority=priority)
                if item is not None:
                    items.append(item)
                continue
            if candidate.concept_id in already:
                continue
            record = self._metadata_index.get(candidate.concept_id)
            if record is None:
                continue
            items.append(
                self._item(
                    record,
                    category=PackCategory.RETRIEVED,
                    priority=priority,
                    reason=candidate.reasons[0],
                )
            )
        return items

    def _sectioned_workspaces(self, scope: ResolvedScope) -> tuple[ConceptId, ...]:
        """Whose sections this pass reads."""
        if self._reach.parent_sections:
            return (scope.workspace_id, *scope.parent_chain)
        return (scope.workspace_id,)

    def _context_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """What someone arriving needs to know first (core/03 section 5.1).

        Before Standards and decisions: orientation precedes rules, and rules
        precede history.
        """
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(types=("context",), workspace_ids=self._sectioned_workspaces(scope))
            )
            if not is_detached(record)
        ]
        return [
            self._item(
                record,
                category=PackCategory.CONTEXT,
                priority=CONTEXT_BAND + min(rank, BAND_SPAN - 1),
                reason=AcquisitionReason.of(ReasonCode.STRUCTURAL_LOCATION, detail="Context/"),
            )
            for rank, record in enumerate(sorted(records, key=lambda r: str(r.path)))
        ]

    def _applicable_standards(self, scope: ResolvedScope) -> list[MetadataRecord]:
        """Standards binding this workspace, its parents, or the whole vault.

        A vault-wide Standard has no workspace of its own and applies to
        everything, which is why the absence of a workspace includes rather than
        excludes it.
        """
        applicable = {scope.workspace_id, *scope.parent_chain}
        return [
            record
            for record in self._metadata_index.query(MetadataQuery(types=("standard",)))
            if not is_detached(record)
            and (record.workspace_id is None or record.workspace_id in applicable)
        ]

    def _standard_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """Every standard that applies, with what each says it governs."""
        records = self._applicable_standards(scope)
        return [
            replace(
                self._item(
                    record,
                    category=PackCategory.STANDARD,
                    priority=STANDARD_BAND + min(rank, BAND_SPAN - 1),
                    reason=AcquisitionReason.of(ReasonCode.METADATA_FILTER, detail="applies_to"),
                ),
                # Every standard that still applies is binding. A cap on
                # standards is incoherent: a document is a standard *because* it
                # has to be read and applied, and a rule obeyed only while the
                # first six last is not a rule. The character budget is the
                # bound instead: over it, a standard becomes a reference and the
                # reader still learns the rule exists.
                #
                # `90_Archive/` is excluded above. A retired standard has no
                # workspace either, so without that filter every archived
                # standard would be marked binding. Archiving is how a rule
                # stops applying (core/01 section 11).
                binding=True,
                description=_description(record),
            )
            for rank, record in enumerate(standards_by_breadth(records))
        ]

    def _decision_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """The workspace's decisions, ranked by authority.

        An accepted decision outranks a superseded one. That ranking is
        mechanical (it reads frontmatter lifecycle), and
        deliberately keeps the lower ranks rather than filtering them out: a
        superseded decision is still context, and a per-category cap is the right
        place to decide how much of it fits.

        **An accepted decision is binding, for the reason a live standard is.**
        `_standard_items` already exempts every rule still in force, because "a
        document is a standard *because* it has to be read and applied". A
        decision with `lifecycle: accepted` is a ruling in force by exactly the
        same test, and a decision that binds only while the first eight last is
        not a decision. The cap keeps its job over the ranks that are *context*
        rather than law -- proposed, superseded, rejected -- and the character
        budget stays the real bound, where an accepted decision degrades to a
        reference and the reader still learns it exists. Capping accepted
        decisions would withhold whichever ones sort last, which tends to be
        the newest and most relevant.
        """
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(types=("decision",), workspace_ids=self._sectioned_workspaces(scope))
            )
            if not is_detached(record)
        ]
        # The rank is the position in *this* order, not the lifecycle rank
        # alone. `apply_budget` sorts by priority and breaks ties on identity,
        # so a band where every accepted decision shares one priority is a band
        # ordered by UUID, which is creation order in disguise. Under a cap,
        # that would decide which ones a reader never sees.
        #
        # Within a lifecycle rank the newest decision leads. Path order is a
        # filename's opinion about importance, and for `adr-NNNN_` filenames it
        # is chronological *backwards* -- the oldest ruling first and the one
        # that overturned it last. `created_at` descending puts the current
        # ruling in front of the record it replaced; the path still breaks the
        # remaining ties, so the order stays fully deterministic.
        return [
            replace(
                self._item(
                    record,
                    category=PackCategory.DECISION,
                    priority=DECISION_BAND + min(rank, BAND_SPAN - 1),
                    reason=AcquisitionReason.of(
                        ReasonCode.METADATA_FILTER,
                        detail=f"lifecycle {record.lifecycle or 'unstated'}",
                    ),
                ),
                binding=record.lifecycle == _ACCEPTED,
            )
            for rank, record in enumerate(_by_authority_then_recency(records))
        ]

    def _goal_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """What this workspace is trying to achieve, while it is still open.

        core/07 section 9 lists "active goal titles" among what a non-LLM
        summary should carry. An achieved or abandoned goal is history: still
        reachable by asking, not delivered unasked.
        """
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(types=("goal",), workspace_ids=self._sectioned_workspaces(scope))
            )
            if not is_detached(record) and record.lifecycle in _OPEN_GOAL_LIFECYCLES
        ]
        return [
            self._item(
                record,
                category=PackCategory.GOAL,
                priority=GOAL_BAND + min(rank, BAND_SPAN - 1),
                reason=AcquisitionReason.of(
                    ReasonCode.METADATA_FILTER, detail=f"lifecycle {record.lifecycle or 'unstated'}"
                ),
            )
            for rank, record in enumerate(sorted(records, key=lambda r: str(r.path)))
        ]

    def _plan_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """How it intends to get there, for plans still in play.

        **The newest plan leads.** Path order is not a ranking, and under a cap
        it would drop plans for their filenames. "Current plan" is a claim about
        recency, so recency is what orders it; the path still breaks ties, so
        the result stays deterministic.

        Unlike a decision, a plan is not law and the cap keeps its job here. An
        old plan that still says `active` is exactly what a cap should absorb.
        """
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(types=("plan",), workspace_ids=self._sectioned_workspaces(scope))
            )
            if not is_detached(record) and record.lifecycle in _OPEN_PLAN_LIFECYCLES
        ]
        return [
            self._item(
                record,
                category=PackCategory.PLAN,
                priority=PLAN_BAND + min(rank, BAND_SPAN - 1),
                reason=AcquisitionReason.of(
                    ReasonCode.METADATA_FILTER, detail=f"lifecycle {record.lifecycle or 'unstated'}"
                ),
            )
            for rank, record in enumerate(_newest_first(records))
        ]

    def _walkthrough_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """The walkthrough of each phase in progress, named and read on demand.

        A walkthrough is the step-by-step record of the phase a session is
        most likely there to continue, so it is named in every startup with
        the reason it is there. It is carried as a reference, never a body:
        one can run to tens of thousands of characters, and the session reads
        it when its work touches the phase (core/02 section 21.27).
        """
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(
                    types=("walkthrough",), workspace_ids=self._sectioned_workspaces(scope)
                )
            )
            if not is_detached(record) and record.lifecycle == _IN_PROGRESS
        ]
        return [
            self._item(
                record,
                category=PackCategory.WALKTHROUGH,
                priority=PLAN_BAND + min(rank, BAND_SPAN - 1),
                reason=AcquisitionReason.of(
                    ReasonCode.METADATA_FILTER,
                    detail=_phase_detail(record),
                ),
            ).as_reference()
            for rank, record in enumerate(_newest_first(records))
        ]

    def _task_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """Work still open in this workspace.

        Vault-native tasks only. Tracker-sourced work items arrive as *signals*
        through a WorkManagementSignalProvider, because they are
        external operational state rather than canonical documents -- the pack's
        shape does not change when they do.
        """
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(types=("task",), workspace_ids=self._sectioned_workspaces(scope))
            )
            if record.lifecycle not in _CLOSED_TASK_LIFECYCLES and not is_detached(record)
        ]
        return [
            self._item(
                record,
                category=PackCategory.TASK,
                priority=TASK_BAND + min(rank, BAND_SPAN - 1),
                reason=AcquisitionReason.of(
                    ReasonCode.WORK_ITEM_CURRENT,
                    detail=f"lifecycle {record.lifecycle or 'unstated'}",
                ),
            )
            for rank, record in enumerate(sorted(records, key=lambda r: str(r.path)))
        ]

    def _activity_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """What happened here recently, newest first."""
        records = [
            record
            for record in self._metadata_index.query(
                MetadataQuery(
                    types=("activity_log",), workspace_ids=self._sectioned_workspaces(scope)
                )
            )
            if not is_detached(record)
        ]
        ordered = sorted(records, key=occurred_at, reverse=True)
        return [
            self._item(
                record,
                category=PackCategory.ACTIVITY,
                priority=ACTIVITY_BAND + position,
                reason=AcquisitionReason.of(ReasonCode.RECENT_ACTIVITY, detail=occurred_at(record)),
            )
            for position, record in enumerate(ordered)
        ]

    def _workspace_items(self, scope: ResolvedScope) -> list[ContextItem]:
        """The workspace manifest, then its parent chain outward.

        Nothing else: core/07 section 6 reduces the candidate universe before
        anything expensive, and the whole vault is never loaded by default.
        """
        wanted: list[tuple[ConceptId, AcquisitionReason, int]] = [
            (scope.workspace_id, scope.reason, 0)
        ]
        wanted.extend(
            (
                parent_id,
                AcquisitionReason.of(ReasonCode.PARENT_WORKSPACE, detail=f"depth {depth}"),
                depth,
            )
            for depth, parent_id in enumerate(scope.parent_chain, start=1)
        )

        items: list[ContextItem] = []
        for concept_id, reason, priority in wanted:
            record = self._metadata_index.get(concept_id)
            if record is None:
                continue
            items.append(
                self._item(
                    record,
                    category=PackCategory.WORKSPACE if priority == 0 else PackCategory.PARENT,
                    priority=priority,
                    reason=reason,
                )
            )
        return items

    def _item(
        self,
        record: MetadataRecord,
        *,
        category: PackCategory,
        priority: int,
        reason: AcquisitionReason,
    ) -> ContextItem:
        document = self._document_for(record)
        return ContextItem(
            concept_id=record.concept_id,
            path=record.path,
            title=record.title,
            priority=priority,
            reason=reason,
            body=document.body if document is not None else None,
            category=category,
            stale_since=stale_since(document, self._now()),
            size=len(document.body) if document is not None else None,
        )

    def _document_for(self, record: MetadataRecord) -> StoredDocument | None:
        if self._document_store is None:
            return None
        return self._document_store.get(record.concept_id)


def _phase_detail(record: MetadataRecord) -> str:
    phase = record.extra.get("phase")
    named = f", phase {phase}" if isinstance(phase, str) and phase.strip() else ""
    return f"current phase walkthrough{named}"


def _description(record: MetadataRecord) -> str | None:
    value = record.extra.get("description")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _is_required(
    item: ContextItem,
    scope: ResolvedScope,
    handoff: ConceptId | None,
    standards: frozenset[str],
) -> bool:
    """Required reading for a startup in this scope (core/07 section 10).

    The workspace and its parents' pages, every context document -- a startup
    reaches only the workspace's own `Context/` -- the newest handoff, and the
    standards `never4ga.context.required` names.
    """
    if item.category in (PackCategory.WORKSPACE, PackCategory.PARENT, PackCategory.CONTEXT):
        return True
    if item.category == PackCategory.STANDARD:
        return item.concept_id is not None and str(item.concept_id) in standards
    return item.category == PackCategory.ACTIVITY and item.concept_id == handoff
