"""Deep context: the third progressive mode.

Deep is not a fourth kind of retrieval, so these tests are mostly about
*difference*: what a deep pack holds that a startup or focused pack does not,
and what it still refuses to hold at any width.

Four widenings, one per class below, and one rule that does not widen: the pack
stays inside the workspace and its parents. A depth that crossed to a sibling
would no longer be bounded.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from never4ga.adapters.fakes import (
    InMemoryGraphIndex,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.context.assembler import StartupContextAssembler
from never4ga.context.budget import deep_budget, startup_budget
from never4ga.context.deep import DeepContextAssembler
from never4ga.context.focused import FocusedContextAssembler
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.context import (
    ContextBudget,
    ContextDepth,
    ContextPack,
    ContextRequest,
    PackCategory,
)
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.graph_index import GraphEdge
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.ports.text_index import IndexedChunk

PARENT = ConceptId.new()
CHILD = ConceptId.new()
SIBLING = ConceptId.new()

REPO = PurePosixPath("/home/user/Projects/never4ga")

CHILD_DECISION = ConceptId.new()
PARENT_DECISION = ConceptId.new()
SIBLING_DECISION = ConceptId.new()

#: A `30_Knowledge/` note belongs to no workspace, so a workspace filter
#: excludes it. Deep admits it; focused does not.
KNOWLEDGE_NOTE = ConceptId.new()

#: The same thing, archived. It keeps `type: knowledge` -- archiving moves a
#: file, it does not restamp it -- so a type-based admission takes it too.
ARCHIVED_NOTE = ConceptId.new()

#: A lexical hit and the chain hanging off it. Focused traverses one hop and
#: reaches FIRST_HOP; deep takes a second and reaches SECOND_HOP.
LEXICAL_HIT = ConceptId.new()
FIRST_HOP = ConceptId.new()
SECOND_HOP = ConceptId.new()


@pytest.fixture
def registry() -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=CHILD,
            workspace_path=VaultPath.parse("10_Workspaces/BezaCore/Workspaces/n4g/workspace.md"),
            repository_root=REPO,
            parent_id=PARENT,
        )
    )
    return registry


@pytest.fixture
def metadata() -> InMemoryMetadataIndex:
    index = InMemoryMetadataIndex()
    records = (
        (PARENT, "workspace", "BezaCore Labs", None, "10_Workspaces/BezaCore/workspace.md"),
        (
            CHILD,
            "workspace",
            "Never4gA",
            None,
            "10_Workspaces/BezaCore/Workspaces/n4g/workspace.md",
        ),
        (
            SIBLING,
            "workspace",
            "Sparrow",
            None,
            "10_Workspaces/BezaCore/Workspaces/ig/workspace.md",
        ),
        (
            CHILD_DECISION,
            "decision",
            "ADR-0018 Skills",
            CHILD,
            "10_Workspaces/BezaCore/Workspaces/n4g/Decisions/adr-0018.md",
        ),
        (
            PARENT_DECISION,
            "decision",
            "Bookkeeping cadence",
            PARENT,
            "10_Workspaces/BezaCore/Decisions/bookkeeping.md",
        ),
        (
            SIBLING_DECISION,
            "decision",
            "Someone else's ruling",
            SIBLING,
            "10_Workspaces/BezaCore/Workspaces/ig/Decisions/theirs.md",
        ),
        (KNOWLEDGE_NOTE, "knowledge", "Hybrid retrieval", None, "30_Knowledge/Notes/hybrid.md"),
        (
            ARCHIVED_NOTE,
            "knowledge",
            "Hybrid retrieval, the old way",
            None,
            "90_Archive/Knowledge/Notes/hybrid.md",
        ),
        (
            LEXICAL_HIT,
            "research_note",
            "Fusion",
            CHILD,
            "10_Workspaces/BezaCore/Workspaces/n4g/Research/fusion.md",
        ),
        (
            FIRST_HOP,
            "research_note",
            "Lanes",
            CHILD,
            "10_Workspaces/BezaCore/Workspaces/n4g/Research/lanes.md",
        ),
        (
            SECOND_HOP,
            "research_note",
            "Ranking",
            CHILD,
            "10_Workspaces/BezaCore/Workspaces/n4g/Research/ranking.md",
        ),
    )
    for concept_id, concept_type, title, workspace, path in records:
        index.upsert(
            MetadataRecord(
                concept_id=concept_id,
                concept_type=concept_type,
                path=VaultPath.parse(path),
                title=title,
                workspace_id=workspace,
                lifecycle="accepted" if concept_type == "decision" else None,
            )
        )
    return index


@pytest.fixture
def text() -> InMemoryTextIndex:
    index = InMemoryTextIndex()
    for concept_id, body in (
        (LEXICAL_HIT, "reciprocal rank fusion over three lanes"),
        (KNOWLEDGE_NOTE, "hybrid retrieval combines fusion with vectors"),
        # Indexed with the same words as the live note, so nothing but its
        # location can be what excludes it.
        (ARCHIVED_NOTE, "hybrid retrieval combines fusion with vectors"),
    ):
        index.index_chunk(
            IndexedChunk(
                chunk=ChunkIdentity(
                    concept_id=concept_id,
                    heading_path=(),
                    ordinal=0,
                    content_hash="abc123",
                    policy_version="v1",
                ),
                path=VaultPath.parse("30_Knowledge/Notes/thing.md"),
                text=body,
                line_range=LineRange(start=1, end=1),
            )
        )
    return index


@pytest.fixture
def graph() -> InMemoryGraphIndex:
    index = InMemoryGraphIndex()
    index.add_edge(GraphEdge(source=LEXICAL_HIT, target=FIRST_HOP, relation_type="depends_on"))
    index.add_edge(GraphEdge(source=FIRST_HOP, target=SECOND_HOP, relation_type="depends_on"))
    return index


@pytest.fixture
def deep(
    registry: WorkspaceRegistry,
    metadata: InMemoryMetadataIndex,
    text: InMemoryTextIndex,
    graph: InMemoryGraphIndex,
) -> DeepContextAssembler:
    return DeepContextAssembler(
        resolver=MechanicalScopeResolver(registry),
        metadata_index=metadata,
        text_index=text,
        graph_index=graph,
    )


@pytest.fixture
def focused(
    registry: WorkspaceRegistry,
    metadata: InMemoryMetadataIndex,
    text: InMemoryTextIndex,
    graph: InMemoryGraphIndex,
) -> FocusedContextAssembler:
    return FocusedContextAssembler(
        resolver=MechanicalScopeResolver(registry),
        metadata_index=metadata,
        text_index=text,
        graph_index=graph,
    )


@pytest.fixture
def startup(
    registry: WorkspaceRegistry, metadata: InMemoryMetadataIndex
) -> StartupContextAssembler:
    return StartupContextAssembler(
        resolver=MechanicalScopeResolver(registry), metadata_index=metadata
    )


def pack_of(
    assembler: DeepContextAssembler | FocusedContextAssembler | StartupContextAssembler,
    depth: ContextDepth,
    *terms: str,
    budget: ContextBudget | None = None,
) -> ContextPack:
    return assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO / "src"),
            depth=depth,
            budget=budget or (deep_budget() if depth is ContextDepth.DEEP else startup_budget()),
            terms=terms,
        )
    )


def ids(pack: ContextPack) -> list[ConceptId | None]:
    return [item.concept_id for item in pack.items]


class TestItReachesFurtherUpTheChain:
    def test_a_parents_own_decisions_arrive(self, deep: DeepContextAssembler) -> None:
        assert PARENT_DECISION in ids(pack_of(deep, ContextDepth.DEEP))

    def test_startup_leaves_them_out(self, startup: StartupContextAssembler) -> None:
        # Not a defect in startup: a session opening in a child needs to know
        # the parent exists far more than it needs the parent's decision log.
        assert PARENT_DECISION not in ids(pack_of(startup, ContextDepth.STARTUP))

    def test_the_workspaces_own_decisions_are_still_there(self, deep: DeepContextAssembler) -> None:
        assert CHILD_DECISION in ids(pack_of(deep, ContextDepth.DEEP))


class TestItAdmitsKnowledge:
    def test_a_knowledge_note_can_be_retrieved(self, deep: DeepContextAssembler) -> None:
        assert KNOWLEDGE_NOTE in ids(pack_of(deep, ContextDepth.DEEP, "fusion"))

    def test_focused_excludes_it(self, focused: FocusedContextAssembler) -> None:
        # `30_Knowledge/` belongs to no workspace, so the Stage B reduction
        # drops it however well it matches.
        assert KNOWLEDGE_NOTE not in ids(pack_of(focused, ContextDepth.FOCUSED, "fusion"))


class TestItLeavesTheArchiveWhereItWasPut:
    """`90_Archive/` is excluded from retrieval at every depth (core/07 section 3).

    Deep admits knowledge by type, and an archived note keeps its type because
    archiving moves a file without restamping it. The exclusion has to be by
    location, or an agent would read superseded material beside the live answer.
    """

    def test_an_archived_note_never_arrives(self, deep: DeepContextAssembler) -> None:
        assert ARCHIVED_NOTE not in ids(pack_of(deep, ContextDepth.DEEP, "fusion"))

    def test_and_the_live_one_still_does(self, deep: DeepContextAssembler) -> None:
        # The exclusion is by location. A rule that dropped both would have
        # passed the test above and broken the thing deep exists for.
        assert KNOWLEDGE_NOTE in ids(pack_of(deep, ContextDepth.DEEP, "fusion"))

    def test_focused_never_had_it_either(self, focused: FocusedContextAssembler) -> None:
        assert ARCHIVED_NOTE not in ids(pack_of(focused, ContextDepth.FOCUSED, "fusion"))


class TestItTakesASecondGraphHop:
    def test_the_neighbour_of_a_neighbour_arrives(self, deep: DeepContextAssembler) -> None:
        assert SECOND_HOP in ids(pack_of(deep, ContextDepth.DEEP, "fusion"))

    def test_focused_stops_at_the_first(self, focused: FocusedContextAssembler) -> None:
        found = ids(pack_of(focused, ContextDepth.FOCUSED, "fusion"))
        assert FIRST_HOP in found
        assert SECOND_HOP not in found

    def test_the_hop_it_was_reached_at_is_recorded(self, deep: DeepContextAssembler) -> None:
        pack = pack_of(deep, ContextDepth.DEEP, "fusion")
        reached = next(item for item in pack.items if item.concept_id == SECOND_HOP)
        assert "depth 2" in (reached.reason.detail or "")


class TestItStaysInsideTheLineage:
    def test_a_sibling_workspaces_decision_never_arrives(self, deep: DeepContextAssembler) -> None:
        # The rule that does not widen. Crossing to a sibling is an explicit
        # named argument, never a depth.
        assert SIBLING_DECISION not in ids(pack_of(deep, ContextDepth.DEEP, "ruling"))

    def test_the_sibling_workspace_itself_never_arrives(self, deep: DeepContextAssembler) -> None:
        assert SIBLING not in ids(pack_of(deep, ContextDepth.DEEP, "Sparrow"))


class TestItIsStillBounded:
    def test_the_budget_binds(self, deep: DeepContextAssembler) -> None:
        pack = pack_of(
            deep,
            ContextDepth.DEEP,
            "fusion",
            budget=ContextBudget(max_items=2, max_characters=1000),
        )
        # Two optional items, plus the binding ones. A binding item, such as an
        # accepted decision, is exempt from the item ceiling.
        optional = [item for item in pack.items if not item.binding]
        assert len(optional) == 2

    def test_a_binding_item_survives_a_budget_that_admits_nothing(
        self, deep: DeepContextAssembler
    ) -> None:
        # The character budget is the one bound a binding item does not escape,
        # and it degrades rather than drops: the reader still learns the
        # decision exists and can ask for it. That keeps the item-ceiling
        # exemption safe, since references are bounded in characters.
        pack = pack_of(
            deep,
            ContextDepth.DEEP,
            "fusion",
            budget=ContextBudget(max_items=1, max_characters=1),
        )
        binding = [item for item in pack.items if item.binding]
        assert binding
        assert all(item.is_reference for item in binding)

    def test_a_deep_budget_is_wider_than_a_startup_one(self) -> None:
        assert deep_budget().max_items > startup_budget().max_items
        assert deep_budget().max_characters > startup_budget().max_characters

    def test_logs_reach_further_back(self) -> None:
        activity = PackCategory.ACTIVITY.value
        assert deep_budget().limit_for(activity) > startup_budget().limit_for(activity)  # type: ignore[operator]

    def test_no_llm_stage_runs(self, deep: DeepContextAssembler) -> None:
        assert pack_of(deep, ContextDepth.DEEP, "fusion").llm_stages == ()

    def test_every_item_says_why_it_is_there(self, deep: DeepContextAssembler) -> None:
        pack = pack_of(deep, ContextDepth.DEEP, "fusion")
        assert pack.items
        assert all(item.reason.is_mechanical for item in pack.items)

    def test_results_are_deterministic(self, deep: DeepContextAssembler) -> None:
        assert ids(pack_of(deep, ContextDepth.DEEP, "fusion")) == ids(
            pack_of(deep, ContextDepth.DEEP, "fusion")
        )


class TestStructureOutranksSearch:
    def test_the_workspace_leads_the_pack(self, deep: DeepContextAssembler) -> None:
        assert ids(pack_of(deep, ContextDepth.DEEP, "fusion"))[0] == CHILD

    def test_a_document_found_twice_appears_once(self, deep: DeepContextAssembler) -> None:
        found = ids(pack_of(deep, ContextDepth.DEEP, "ADR-0018"))
        assert found.count(CHILD_DECISION) == 1

    def test_a_tight_budget_degrades_to_what_the_workspace_is(
        self, deep: DeepContextAssembler
    ) -> None:
        pack = pack_of(
            deep,
            ContextDepth.DEEP,
            "fusion",
            budget=ContextBudget(max_items=2, max_characters=1000),
        )
        assert [item.category for item in pack.items if not item.binding] == [
            PackCategory.WORKSPACE,
            PackCategory.PARENT,
        ]


class TestDepth:
    def test_a_deep_pack_is_at_least_as_wide_as_a_startup_one(
        self, deep: DeepContextAssembler, startup: StartupContextAssembler
    ) -> None:
        assert len(pack_of(deep, ContextDepth.DEEP, "fusion").items) >= len(
            pack_of(startup, ContextDepth.STARTUP).items
        )

    def test_startup_and_focused_are_refused_by_the_deep_assembler(
        self, deep: DeepContextAssembler
    ) -> None:
        for depth in (ContextDepth.STARTUP, ContextDepth.FOCUSED):
            with pytest.raises(NotImplementedError, match="builds deep packs only"):
                pack_of(deep, depth, "fusion")

    def test_a_pack_with_no_terms_is_still_a_pack(self, deep: DeepContextAssembler) -> None:
        # `context startup --depth deep` gives it nothing to search for. It is a
        # wider startup pack, not an empty one.
        assert ids(pack_of(deep, ContextDepth.DEEP))
