"""What a startup Context Pack actually contains.

core/07 fixes the *stages* of acquisition and does not enumerate the pack. A
startup pack holds the workspace manifest, its parent chain, Standards that
apply, decisions ranked by authority, open work items, and recent activity --
and nothing from other workspaces or life areas. Accepted decisions outrank
superseded ones.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from never4ga.adapters.fakes import InMemoryMetadataIndex
from never4ga.context.assembler import StartupContextAssembler
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.context import (
    ContextBudget,
    ContextDepth,
    ContextPack,
    ContextRequest,
    PackCategory,
)
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import ReasonCode
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.metadata_index import MetadataRecord

PARENT = ConceptId.new()
WORKSPACE = ConceptId.new()
OTHER = ConceptId.new()

REPO = PurePosixPath("/home/user/Projects/never4ga")
OTHER_REPO = PurePosixPath("/home/user/Projects/other")

ACCEPTED = ConceptId.new()
OLDER_ACCEPTED = ConceptId.new()
SUPERSEDED = ConceptId.new()
PROPOSED = ConceptId.new()


def record(
    concept_id: ConceptId,
    concept_type: str,
    title: str,
    *,
    workspace_id: ConceptId | None = WORKSPACE,
    lifecycle: str | None = None,
    authority: str | None = None,
    path: str | None = None,
    **extra: object,
) -> MetadataRecord:
    return MetadataRecord(
        concept_id=concept_id,
        concept_type=concept_type,
        path=VaultPath.parse(path or f"10_Workspaces/never4ga/{concept_type}/{title}.md"),
        title=title,
        workspace_id=workspace_id,
        lifecycle=lifecycle,
        authority=authority,
        extra=extra,
    )


@pytest.fixture
def registry() -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=PARENT,
            workspace_path=VaultPath.parse("10_Workspaces/BezaCore-Labs/workspace.md"),
        )
    )
    registry.register(
        WorkspaceMapping(
            workspace_id=WORKSPACE,
            workspace_path=VaultPath.parse(
                "10_Workspaces/BezaCore-Labs/Workspaces/never4ga/workspace.md"
            ),
            repository_root=REPO,
            parent_id=PARENT,
        )
    )
    registry.register(
        WorkspaceMapping(
            workspace_id=OTHER,
            workspace_path=VaultPath.parse("10_Workspaces/unrelated/workspace.md"),
            repository_root=OTHER_REPO,
        )
    )
    return registry


@pytest.fixture
def index() -> InMemoryMetadataIndex:
    index = InMemoryMetadataIndex()
    for entry in (
        record(PARENT, "workspace", "BezaCore-Labs", workspace_id=None),
        record(WORKSPACE, "workspace", "Never4gA", workspace_id=PARENT),
        record(OTHER, "workspace", "Unrelated", workspace_id=None),
        record(
            ACCEPTED,
            "decision",
            "use-sqlite",
            lifecycle="accepted",
            created_at="2026-09-04T00:00:00Z",
        ),
        record(
            OLDER_ACCEPTED,
            "decision",
            "older-accepted",
            lifecycle="accepted",
            created_at="2026-05-14T00:00:00Z",
        ),
        record(SUPERSEDED, "decision", "use-json-index", lifecycle="superseded"),
        record(PROPOSED, "decision", "maybe-neo4j", lifecycle="proposed"),
        record(ConceptId.new(), "standard", "commit-style"),
        record(ConceptId.new(), "context", "start-here"),
        record(ConceptId.new(), "context", "project-state"),
        record(ConceptId.new(), "task", "write-the-resolver", lifecycle="in_progress"),
        record(ConceptId.new(), "task", "finished-thing", lifecycle="done"),
        record(ConceptId.new(), "task", "abandoned-thing", lifecycle="cancelled"),
        record(
            ConceptId.new(),
            "activity_log",
            "2026-08-23",
            occurred_at="2026-08-23T09:00:00+00:00",
        ),
        record(
            ConceptId.new(),
            "activity_log",
            "2026-08-01",
            occurred_at="2026-08-01T09:00:00+00:00",
        ),
        # Belongs to somebody else entirely.
        record(ConceptId.new(), "decision", "not-ours", workspace_id=OTHER),
        record(ConceptId.new(), "task", "not-ours-either", workspace_id=OTHER),
        # A life area, which startup has no business loading.
        record(
            ConceptId.new(),
            "life_area",
            "health",
            workspace_id=None,
            path="20_Life/health/area.md",
        ),
    ):
        index.upsert(entry)
    return index


@pytest.fixture
def assembler(registry: WorkspaceRegistry, index: InMemoryMetadataIndex) -> StartupContextAssembler:
    return StartupContextAssembler(resolver=MechanicalScopeResolver(registry), metadata_index=index)


def pack(assembler: StartupContextAssembler, **budget: object) -> ContextPack:
    limits: dict[str, object] = {"max_items": 50, "max_characters": 100_000}
    limits.update(budget)
    return assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO / "src"),
            depth=ContextDepth.STARTUP,
            budget=ContextBudget(**limits),  # type: ignore[arg-type]
        )
    )


def titles(assembled: ContextPack, category: PackCategory | None = None) -> list[str]:
    return [item.title for item in assembled.items if category is None or item.category == category]


class TestWhatIsIncluded:
    def test_the_workspace_manifest_comes_first(self, assembler: StartupContextAssembler) -> None:
        assert pack(assembler).items[0].title == "Never4gA"

    def test_the_parent_chain_is_included(self, assembler: StartupContextAssembler) -> None:
        assert "BezaCore-Labs" in titles(pack(assembler), PackCategory.PARENT)

    def test_the_workspace_orientation_is_included_before_its_decisions(
        self, assembler: StartupContextAssembler
    ) -> None:
        # core/03 section 5.1: Context is "durable orientation/current-state
        # material". A pack of decisions with nothing saying what state the
        # project is in leaves a reader lost. Orientation comes before history.
        assert set(titles(pack(assembler), PackCategory.CONTEXT)) == {"start-here", "project-state"}
        order = [item.category for item in pack(assembler).items]
        assert order.index(PackCategory.CONTEXT) < order.index(PackCategory.DECISION)

    def test_standards_that_apply_are_included(self, assembler: StartupContextAssembler) -> None:
        assert titles(pack(assembler), PackCategory.STANDARD) == ["commit-style"]

    def test_open_work_items_are_included(self, assembler: StartupContextAssembler) -> None:
        assert titles(pack(assembler), PackCategory.TASK) == ["write-the-resolver"]

    def test_finished_work_is_not_included(self, assembler: StartupContextAssembler) -> None:
        included = titles(pack(assembler))
        assert "finished-thing" not in included
        assert "abandoned-thing" not in included

    def test_recent_activity_is_included_newest_first(
        self, assembler: StartupContextAssembler
    ) -> None:
        assert titles(pack(assembler), PackCategory.ACTIVITY) == ["2026-08-23", "2026-08-01"]


class TestAuthorityRanking:
    def test_an_accepted_decision_outranks_a_superseded_one(
        self, assembler: StartupContextAssembler
    ) -> None:
        # Ranked mechanically, from frontmatter lifecycle, never by asking a
        # model which matters more.
        ranked = titles(pack(assembler), PackCategory.DECISION)
        assert ranked.index("use-sqlite") < ranked.index("use-json-index")

    def test_an_accepted_decision_outranks_a_proposed_one(
        self, assembler: StartupContextAssembler
    ) -> None:
        ranked = titles(pack(assembler), PackCategory.DECISION)
        assert ranked.index("use-sqlite") < ranked.index("maybe-neo4j")

    def test_a_tight_decision_cap_keeps_the_accepted_one(
        self, assembler: StartupContextAssembler
    ) -> None:
        assembled = pack(assembler, category_limits={PackCategory.DECISION: 1})
        assert "use-sqlite" in titles(assembled, PackCategory.DECISION)

    def test_no_cap_can_drop_an_accepted_decision(self, assembler: StartupContextAssembler) -> None:
        # An accepted decision is a ruling in force, and binding for the reason
        # a live standard is: a decision that binds only while the first N last
        # is not a decision. A cap would also tend to drop the newest rulings,
        # the ones governing current work.
        assembled = pack(assembler, category_limits={PackCategory.DECISION: 0})
        assert titles(assembled, PackCategory.DECISION) == ["use-sqlite", "older-accepted"]

    def test_a_cap_still_bounds_decisions_that_are_not_in_force(
        self, assembler: StartupContextAssembler
    ) -> None:
        # The cap keeps its job over the ranks that are context rather than law.
        assembled = pack(assembler, category_limits={PackCategory.DECISION: 0})
        ranked = titles(assembled, PackCategory.DECISION)
        assert "use-json-index" not in ranked
        assert "maybe-neo4j" not in ranked

    def test_the_newest_decision_leads_within_a_lifecycle(
        self, assembler: StartupContextAssembler
    ) -> None:
        # Path order is a filename's opinion about importance, and for
        # `adr-NNNN_` names it is chronological backwards -- the oldest ruling
        # first and the one that overturned it last.
        ranked = titles(pack(assembler), PackCategory.DECISION)
        assert ranked.index("use-sqlite") < ranked.index("older-accepted")


class TestWhatIsExcluded:
    def test_another_workspace_contributes_nothing(
        self, assembler: StartupContextAssembler
    ) -> None:
        included = titles(pack(assembler))
        assert "not-ours" not in included
        assert "not-ours-either" not in included
        assert "Unrelated" not in included

    def test_life_areas_are_excluded(self, assembler: StartupContextAssembler) -> None:
        assert "health" not in titles(pack(assembler))

    def test_the_whole_vault_is_never_loaded(
        self, assembler: StartupContextAssembler, index: InMemoryMetadataIndex
    ) -> None:
        from never4ga.ports.metadata_index import MetadataQuery

        assert len(pack(assembler).items) < len(index.query(MetadataQuery()))


class TestProvenance:
    def test_every_item_says_why_it_is_there(self, assembler: StartupContextAssembler) -> None:
        assert all(item.reason.is_mechanical for item in pack(assembler).items)

    def test_each_category_carries_its_own_reason_code(
        self, assembler: StartupContextAssembler
    ) -> None:
        codes = {item.category: item.reason.code for item in pack(assembler).items}
        assert codes[PackCategory.WORKSPACE] == ReasonCode.WORKSPACE_REQUIRED
        assert codes[PackCategory.PARENT] == ReasonCode.PARENT_WORKSPACE
        assert codes[PackCategory.TASK] == ReasonCode.WORK_ITEM_CURRENT
        assert codes[PackCategory.ACTIVITY] == ReasonCode.RECENT_ACTIVITY
        assert codes[PackCategory.DECISION] == ReasonCode.METADATA_FILTER
        assert codes[PackCategory.CONTEXT] == ReasonCode.STRUCTURAL_LOCATION


class TestPerCategoryCaps:
    def test_a_category_cap_bounds_that_category_alone(
        self, assembler: StartupContextAssembler
    ) -> None:
        assembled = pack(assembler, category_limits={PackCategory.ACTIVITY: 1})
        assert len(titles(assembled, PackCategory.ACTIVITY)) == 1
        assert titles(assembled, PackCategory.DECISION)

    def test_a_cap_holds_even_when_characters_are_under_budget(
        self, assembler: StartupContextAssembler
    ) -> None:
        # The whole point of item caps: a character ceiling alone lets one
        # category crowd out every other. Measured over a category whose items
        # are all cappable -- a binding item is exempt by design, so a decision
        # cap would test the exemption rather than the cap.
        assembled = pack(
            assembler, max_characters=100_000, category_limits={PackCategory.ACTIVITY: 1}
        )
        assert len(titles(assembled, PackCategory.ACTIVITY)) == 1

    def test_an_uncapped_category_is_unbounded_by_category(
        self, assembler: StartupContextAssembler
    ) -> None:
        assert len(titles(pack(assembler), PackCategory.DECISION)) == 4

    def test_dropped_items_are_counted(self, assembler: StartupContextAssembler) -> None:
        assembled = pack(assembler, category_limits={PackCategory.DECISION: 0})
        # Both decisions that are not in force; the accepted one is binding and
        # is admitted rather than dropped.
        assert assembled.usage.dropped_items >= 2
