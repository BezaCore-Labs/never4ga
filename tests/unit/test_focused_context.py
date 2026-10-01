"""Focused context: three retrievers, fused.

core/07 orders the stages and this follows them: scope resolves first, structural
metadata reduces the candidate universe before anything expensive runs, lexical
retrieval runs inside that reduction, and relationship traversal expands from
what it found. Fusion is last, and it is the only place the three lanes meet.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from never4ga.adapters.fakes import (
    InMemoryGraphIndex,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.context.focused import FocusedContextAssembler
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.context import ContextBudget, ContextDepth, ContextPack, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.graph_index import GraphEdge
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.ports.text_index import IndexedChunk

WORKSPACE = ConceptId.new()
OTHER = ConceptId.new()
REPO = PurePosixPath("/home/user/Projects/never4ga")

RESOLVER_NOTE = ConceptId.new()
LINKED_NOTE = ConceptId.new()
UNRELATED_NOTE = ConceptId.new()
FOREIGN_NOTE = ConceptId.new()


@pytest.fixture
def registry() -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=WORKSPACE,
            workspace_path=VaultPath.parse("10_Workspaces/never4ga/workspace.md"),
            repository_root=REPO,
        )
    )
    return registry


@pytest.fixture
def metadata() -> InMemoryMetadataIndex:
    index = InMemoryMetadataIndex()
    for concept_id, title, workspace in (
        (WORKSPACE, "Never4gA", None),
        (RESOLVER_NOTE, "scope resolver", WORKSPACE),
        (LINKED_NOTE, "workspace registry", WORKSPACE),
        (UNRELATED_NOTE, "coffee", WORKSPACE),
        (FOREIGN_NOTE, "scope resolver elsewhere", OTHER),
    ):
        index.upsert(
            MetadataRecord(
                concept_id=concept_id,
                concept_type="knowledge" if workspace else "workspace",
                path=VaultPath.parse(f"30_Knowledge/Notes/{title.replace(' ', '-')}.md"),
                title=title,
                workspace_id=workspace,
            )
        )
    return index


@pytest.fixture
def text(metadata: InMemoryMetadataIndex) -> InMemoryTextIndex:
    index = InMemoryTextIndex()
    for concept_id, body in (
        (RESOLVER_NOTE, "the scope resolver walks up for a git root"),
        (FOREIGN_NOTE, "another scope resolver entirely"),
        (UNRELATED_NOTE, "coffee is unrelated"),
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
    index.add_edge(GraphEdge(source=RESOLVER_NOTE, target=LINKED_NOTE, relation_type="depends_on"))
    return index


@pytest.fixture
def assembler(
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


def focus(assembler: FocusedContextAssembler, *terms: str, **budget: object) -> ContextPack:
    limits: dict[str, object] = {"max_items": 20, "max_characters": 50_000}
    limits.update(budget)
    return assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO / "src"),
            depth=ContextDepth.FOCUSED,
            budget=ContextBudget(**limits),  # type: ignore[arg-type]
            terms=terms,
        )
    )


def ids(pack: ContextPack) -> list[ConceptId | None]:
    return [item.concept_id for item in pack.items]


class TestRetrieval:
    def test_a_lexical_match_is_found(self, assembler: FocusedContextAssembler) -> None:
        assert RESOLVER_NOTE in ids(focus(assembler, "resolver"))

    def test_a_document_that_matches_nothing_is_not_included(
        self, assembler: FocusedContextAssembler
    ) -> None:
        assert UNRELATED_NOTE not in ids(focus(assembler, "resolver"))

    def test_another_workspace_is_excluded_even_on_a_perfect_match(
        self, assembler: FocusedContextAssembler
    ) -> None:
        # core/07 section 6 reduces the candidate universe by structure first.
        assert FOREIGN_NOTE not in ids(focus(assembler, "resolver"))

    def test_a_linked_document_is_pulled_in_by_traversal(
        self, assembler: FocusedContextAssembler
    ) -> None:
        # Stage E: relationships expand from what lexical retrieval found.
        assert LINKED_NOTE in ids(focus(assembler, "resolver"))

    def test_the_lexical_hit_outranks_the_document_it_merely_links_to(
        self, assembler: FocusedContextAssembler
    ) -> None:
        order = ids(focus(assembler, "resolver"))
        assert order.index(RESOLVER_NOTE) < order.index(LINKED_NOTE)

    def test_an_empty_query_returns_no_retrieved_items(
        self, assembler: FocusedContextAssembler
    ) -> None:
        assert focus(assembler).items == ()


class TestProvenanceAndBounds:
    def test_every_item_says_why_it_is_there(self, assembler: FocusedContextAssembler) -> None:
        pack = focus(assembler, "resolver")
        assert pack.items
        assert all(item.reason.is_mechanical for item in pack.items)

    def test_no_llm_stage_runs(self, assembler: FocusedContextAssembler) -> None:
        assert focus(assembler, "resolver").llm_stages == ()

    def test_the_budget_still_binds(self, assembler: FocusedContextAssembler) -> None:
        assert len(focus(assembler, "resolver", max_items=1).items) == 1

    def test_results_are_deterministic(self, assembler: FocusedContextAssembler) -> None:
        assert ids(focus(assembler, "resolver")) == ids(focus(assembler, "resolver"))


class TestDepth:
    def test_startup_is_refused_by_the_focused_assembler(
        self, assembler: FocusedContextAssembler
    ) -> None:
        with pytest.raises(NotImplementedError):
            assembler.assemble(
                ContextRequest(
                    scope=ScopeRequest(cwd=REPO),
                    depth=ContextDepth.STARTUP,
                    budget=ContextBudget(max_items=5, max_characters=1000),
                    terms=("resolver",),
                )
            )

    def test_deep_is_refused_rather_than_silently_downgraded(
        self, assembler: FocusedContextAssembler
    ) -> None:
        # Deep has its own assembler (tests/unit/test_deep_context.py).
        # Answering a deep request with a focused pack would be worse than
        # refusing it, because the caller would build on a depth it never got.
        with pytest.raises(NotImplementedError):
            assembler.assemble(
                ContextRequest(
                    scope=ScopeRequest(cwd=REPO),
                    depth=ContextDepth.DEEP,
                    budget=ContextBudget(max_items=5, max_characters=1000),
                    terms=("resolver",),
                )
            )


class TestCategories:
    def test_a_retrieved_note_is_not_labelled_a_workspace(
        self, assembler: FocusedContextAssembler
    ) -> None:
        # Labelling a knowledge note as a workspace is a small lie an agent has
        # no way to check, and per-category budgets would then mean nothing.
        from never4ga.domain.context import PackCategory

        [item] = [
            item for item in focus(assembler, "resolver").items if item.concept_id == RESOLVER_NOTE
        ]
        assert item.category == PackCategory.RETRIEVED

    def test_a_category_cap_applies_to_a_focused_pack_too(
        self, assembler: FocusedContextAssembler
    ) -> None:
        from never4ga.domain.context import PackCategory

        pack = focus(assembler, "resolver", category_limits={PackCategory.RETRIEVED: 1})
        retrieved = [i for i in pack.items if i.category == PackCategory.RETRIEVED]
        assert len(retrieved) == 1


class TestTheGraphLaneCorroborates:
    """The relational lane votes, rather than only expanding.

    A document both the lexical and graph lanes reach gets a vote from each, so
    fusion can rank a document two retrievers found above one that a single
    retriever put first.

    These build their own indexes because the property needs three documents:
    with ``Direction.BOTH``, one edge between two lexical hits corroborates both
    ends, so the document that must lose has to touch no edge at all.
    """

    ALONE = ConceptId.new()
    LINKED_A = ConceptId.new()
    LINKED_B = ConceptId.new()

    @pytest.fixture
    def indexes(self) -> tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex]:
        metadata = InMemoryMetadataIndex()
        text = InMemoryTextIndex()
        graph = InMemoryGraphIndex()
        metadata.upsert(
            MetadataRecord(
                concept_id=WORKSPACE,
                concept_type="workspace",
                path=VaultPath.parse("10_Workspaces/never4ga/workspace.md"),
                title="Never4gA",
            )
        )
        # Ranked by how many times the term appears, so the order is `alone`,
        # then `a`, then `b`.
        for concept_id, name, body in (
            (self.ALONE, "alone", "widget widget widget widget"),
            (self.LINKED_A, "linked-a", "widget widget widget"),
            (self.LINKED_B, "linked-b", "widget widget"),
        ):
            metadata.upsert(
                MetadataRecord(
                    concept_id=concept_id,
                    concept_type="knowledge",
                    path=VaultPath.parse(f"30_Knowledge/Notes/{name}.md"),
                    title=name,
                    workspace_id=WORKSPACE,
                )
            )
            text.index_chunk(
                IndexedChunk(
                    chunk=ChunkIdentity(
                        concept_id=concept_id,
                        heading_path=(),
                        ordinal=0,
                        content_hash=name,
                        policy_version="chunk/0.1",
                    ),
                    text=body,
                    line_range=LineRange(start=1, end=1),
                    path=VaultPath.parse(f"30_Knowledge/Notes/{name}.md"),
                )
            )
        # Asserted, not derived: these two fixtures are about corroboration,
        # and only a relation somebody wrote corroborates.
        graph.add_edge(
            GraphEdge(source=self.LINKED_A, target=self.LINKED_B, relation_type="depends_on")
        )
        return metadata, text, graph

    def _fused(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> dict[ConceptId | None, object]:
        metadata, text, graph = indexes
        resolver = MechanicalScopeResolver(registry)
        assembler = FocusedContextAssembler(resolver, metadata, text, graph)
        request = ContextRequest(
            scope=ScopeRequest(repository_root=REPO),
            depth=ContextDepth.FOCUSED,
            budget=ContextBudget(max_items=50, max_characters=100_000),
            terms=("widget",),
        )
        fused = assembler.retrieve(request, resolver.resolve(request.scope))
        return {candidate.concept_id: candidate for candidate in fused}

    def test_a_document_both_lanes_reach_gets_a_vote_from_each(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        by_id = self._fused(registry, indexes)
        assert set(by_id[self.LINKED_A].contributions) == {"text", "graph"}  # type: ignore[attr-defined]
        assert set(by_id[self.LINKED_B].contributions) == {"text", "graph"}  # type: ignore[attr-defined]
        assert set(by_id[self.ALONE].contributions) == {"text"}  # type: ignore[attr-defined]

    def test_two_votes_outrank_one_better_lexical_rank(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        """Agreement between lanes outranks a better single-lane rank.

        `alone` is the best lexical match and nothing corroborates it; `a` is
        second and the graph lane found it too. Agreement wins, which is the
        whole claim RRF makes for itself.
        """
        by_id = self._fused(registry, indexes)
        assert by_id[self.LINKED_A].score > by_id[self.ALONE].score  # type: ignore[attr-defined]

    def test_a_cycle_does_not_traverse_forever_and_nothing_votes_twice(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        metadata, text, graph = indexes
        graph.add_edge(
            GraphEdge(source=self.LINKED_B, target=self.LINKED_A, relation_type="depends_on")
        )
        resolver = MechanicalScopeResolver(registry)
        assembler = FocusedContextAssembler(resolver, metadata, text, graph, relation_depth=3)
        request = ContextRequest(
            scope=ScopeRequest(repository_root=REPO),
            depth=ContextDepth.FOCUSED,
            budget=ContextBudget(max_items=50, max_characters=100_000),
            terms=("widget",),
        )
        fused = assembler.retrieve(request, resolver.resolve(request.scope))
        assert len({candidate.concept_id for candidate in fused}) == len(fused)

    def test_a_derived_link_expands_but_does_not_corroborate(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        """A `links_to` edge can expand a pack but cannot add a vote.

        `links_to` is derived from a Markdown link, not a relationship somebody
        asserted. If it corroborated, densely cross-citing documents would vote
        each other to the top of every result.
        """
        metadata, text, _ = indexes
        derived = InMemoryGraphIndex()
        derived.add_edge(
            GraphEdge(source=self.LINKED_A, target=self.LINKED_B, relation_type="links_to")
        )
        by_id = self._fused(registry, (metadata, text, derived))
        # Both ends are lexical hits, so neither may take a second vote from a
        # link somebody merely wrote in a sentence.
        assert set(by_id[self.LINKED_A].contributions) == {"text"}  # type: ignore[attr-defined]
        assert set(by_id[self.LINKED_B].contributions) == {"text"}  # type: ignore[attr-defined]

    def test_an_asserted_relation_still_corroborates(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        by_id = self._fused(registry, indexes)
        assert set(by_id[self.LINKED_B].contributions) == {"text", "graph"}  # type: ignore[attr-defined]

    def test_a_derived_link_still_introduces_a_document_nothing_else_found(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        """A derived link still brings in a document only the graph reached."""
        metadata, text, graph = indexes
        unfound = ConceptId.new()
        metadata.upsert(
            MetadataRecord(
                concept_id=unfound,
                concept_type="knowledge",
                path=VaultPath.parse("30_Knowledge/Notes/unfound.md"),
                title="unfound",
                workspace_id=WORKSPACE,
            )
        )
        graph.add_edge(GraphEdge(source=self.ALONE, target=unfound, relation_type="links_to"))
        by_id = self._fused(registry, (metadata, text, graph))
        assert set(by_id[unfound].contributions) == {"graph"}  # type: ignore[attr-defined]

    def test_supersession_does_not_second_the_document_it_supersedes(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        """The one registered relation whose meaning is negative.

        Every other relation says two documents belong together. This one says
        one of them is out of date, so a second vote from it would raise a
        document for being stale.
        """
        metadata, text, _ = indexes
        graph = InMemoryGraphIndex()
        graph.add_edge(
            GraphEdge(source=self.LINKED_A, target=self.LINKED_B, relation_type="superseded_by")
        )
        by_id = self._fused(registry, (metadata, text, graph))
        assert set(by_id[self.LINKED_A].contributions) == {"text"}  # type: ignore[attr-defined]
        assert set(by_id[self.LINKED_B].contributions) == {"text"}  # type: ignore[attr-defined]

    def test_closing_a_plan_does_not_second_it(
        self,
        registry: WorkspaceRegistry,
        indexes: tuple[InMemoryMetadataIndex, InMemoryTextIndex, InMemoryGraphIndex],
    ) -> None:
        """`closes` is a lifecycle relation, like `superseded_by`.

        If it voted, each completion report and the plan it closed would vouch
        for each other and rise together. Finishing a plan's work does not make
        either document a better answer to a question.
        """
        metadata, text, _ = indexes
        graph = InMemoryGraphIndex()
        graph.add_edge(
            GraphEdge(source=self.LINKED_A, target=self.LINKED_B, relation_type="closes")
        )
        by_id = self._fused(registry, (metadata, text, graph))
        assert set(by_id[self.LINKED_A].contributions) == {"text"}  # type: ignore[attr-defined]
        assert set(by_id[self.LINKED_B].contributions) == {"text"}  # type: ignore[attr-defined]
