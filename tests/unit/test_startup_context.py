"""Startup context assembly (core/07 sections 15 and 16).

The mechanical-first acceptance tests, exercised end to end.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

import pytest

from never4ga.adapters.fakes import InMemoryDocumentStore, InMemoryMetadataIndex
from never4ga.context.assembler import StartupContextAssembler
from never4ga.context.budget import startup_budget
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest, WorkspaceMapping
from never4ga.domain.signals import ContextSignal
from never4ga.errors import ScopeResolutionError
from never4ga.ports.metadata_index import MetadataRecord

PARENT_ID = ConceptId.new()
CHILD_ID = ConceptId.new()
REPO = PurePosixPath("/home/user/Projects/never4ga")


class RecordingSignalProvider:
    """A mechanical provider that records whether it was asked."""

    provider_id = "recording"

    def __init__(self) -> None:
        self.calls = 0

    def supports(self, request: ContextRequest) -> bool:
        return True

    def collect(self, request: ContextRequest, scope: ResolvedScope) -> Sequence[ContextSignal]:
        self.calls += 1
        return (
            ContextSignal(
                provider_id=self.provider_id,
                kind="git.branch",
                value="milestone-0",
                reason=AcquisitionReason.of(ReasonCode.GIT_CHANGED_FILE),
            ),
        )


class UnsupportedSignalProvider(RecordingSignalProvider):
    provider_id = "unsupported"

    def supports(self, request: ContextRequest) -> bool:
        return False


class ExplodingSignalProvider:
    """An optional provider that is broken."""

    provider_id = "exploding"

    def supports(self, request: ContextRequest) -> bool:
        return True

    def collect(self, request: ContextRequest, scope: ResolvedScope) -> Sequence[ContextSignal]:
        raise RuntimeError("provider is broken")


@pytest.fixture
def registry() -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=PARENT_ID,
            workspace_path=VaultPath.parse("10_Workspaces/never4ga/workspace.md"),
            repository_root=REPO,
        )
    )
    registry.register(
        WorkspaceMapping(
            workspace_id=CHILD_ID,
            workspace_path=VaultPath.parse(
                "10_Workspaces/never4ga/Workspaces/runtime/workspace.md"
            ),
            repository_root=REPO / "runtime",
            parent_id=PARENT_ID,
        )
    )
    return registry


@pytest.fixture
def metadata_index() -> InMemoryMetadataIndex:
    index = InMemoryMetadataIndex()
    index.upsert(
        MetadataRecord(
            concept_id=PARENT_ID,
            concept_type="workspace",
            path=VaultPath.parse("10_Workspaces/never4ga/workspace.md"),
            title="Never4gA",
        )
    )
    index.upsert(
        MetadataRecord(
            concept_id=CHILD_ID,
            concept_type="workspace",
            path=VaultPath.parse("10_Workspaces/never4ga/Workspaces/runtime/workspace.md"),
            title="Never4gA Runtime",
            workspace_id=PARENT_ID,
        )
    )
    return index


@pytest.fixture
def document_store(metadata_index: InMemoryMetadataIndex) -> InMemoryDocumentStore:
    from never4ga.ports.metadata_index import MetadataQuery

    store = InMemoryDocumentStore()
    for record in metadata_index.query(MetadataQuery()):
        store.put(
            StoredDocument(
                concept_id=record.concept_id,
                path=record.path,
                frontmatter={"type": record.concept_type, "title": record.title},
                body=f"# {record.title}\n\nWorkspace manifest body.\n",
            )
        )
    return store


@pytest.fixture
def assembler(
    registry: WorkspaceRegistry,
    metadata_index: InMemoryMetadataIndex,
    document_store: InMemoryDocumentStore,
) -> StartupContextAssembler:
    return StartupContextAssembler(
        resolver=MechanicalScopeResolver(registry),
        metadata_index=metadata_index,
        document_store=document_store,
    )


def startup_request(**overrides: object) -> ContextRequest:
    defaults: dict[str, object] = {
        "scope": ScopeRequest(cwd=REPO / "src"),
        "depth": ContextDepth.STARTUP,
        "budget": ContextBudget(max_items=10, max_characters=10_000),
    }
    defaults.update(overrides)
    return ContextRequest(**defaults)  # type: ignore[arg-type]


class TestStartupIsMechanical:
    def test_scope_is_resolved_from_the_working_directory(
        self, assembler: StartupContextAssembler
    ) -> None:
        pack = assembler.assemble(startup_request())
        assert pack.scope.workspace_id == PARENT_ID

    def test_no_llm_is_involved(self, assembler: StartupContextAssembler) -> None:
        pack = assembler.assemble(startup_request())
        assert pack.used_llm is False
        assert pack.llm_stages == ()

    def test_every_item_and_signal_is_mechanically_acquired(
        self, assembler: StartupContextAssembler
    ) -> None:
        pack = assembler.assemble(startup_request())
        assert pack.items
        assert all(item.reason.is_mechanical for item in pack.items)
        assert all(signal.reason.is_mechanical for signal in pack.signals)

    def test_every_item_reports_why_it_is_present(self, assembler: StartupContextAssembler) -> None:
        # core/07 section 12.
        pack = assembler.assemble(startup_request())
        assert all(item.reason.code for item in pack.items)

    def test_the_workspace_manifest_is_required_context(
        self, assembler: StartupContextAssembler
    ) -> None:
        pack = assembler.assemble(startup_request())
        (workspace_item,) = [i for i in pack.items if i.concept_id == PARENT_ID]
        assert workspace_item.reason.code == ReasonCode.WORKSPACE_REQUIRED

    def test_the_parent_chain_is_included_with_its_own_reason(
        self, assembler: StartupContextAssembler
    ) -> None:
        pack = assembler.assemble(startup_request(scope=ScopeRequest(cwd=REPO / "runtime" / "src")))
        reasons = {item.concept_id: item.reason.code for item in pack.items}
        assert reasons[CHILD_ID] == ReasonCode.WORKSPACE_REQUIRED
        assert reasons[PARENT_ID] == ReasonCode.PARENT_WORKSPACE

    def test_the_whole_vault_is_never_loaded(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        for i in range(50):
            metadata_index.upsert(
                MetadataRecord(
                    concept_id=ConceptId.new(),
                    concept_type="knowledge",
                    path=VaultPath.parse(f"30_Knowledge/Notes/{i}.md"),
                    title=f"Note {i}",
                )
            )
        pack = assembler.assemble(startup_request())
        assert len(pack.items) == 1

    def test_an_unresolvable_scope_fails_rather_than_guessing(
        self, assembler: StartupContextAssembler
    ) -> None:
        with pytest.raises(ScopeResolutionError):
            assembler.assemble(startup_request(scope=ScopeRequest(cwd=PurePosixPath("/nope"))))


class TestSignalProviders:
    def test_supporting_providers_contribute_signals(
        self, registry: WorkspaceRegistry, metadata_index: InMemoryMetadataIndex
    ) -> None:
        provider = RecordingSignalProvider()
        assembler = StartupContextAssembler(
            resolver=MechanicalScopeResolver(registry),
            metadata_index=metadata_index,
            signal_providers=(provider,),
        )
        pack = assembler.assemble(startup_request())
        assert provider.calls == 1
        assert [signal.kind for signal in pack.signals] == ["git.branch"]

    def test_unsupported_providers_are_not_called(
        self, registry: WorkspaceRegistry, metadata_index: InMemoryMetadataIndex
    ) -> None:
        provider = UnsupportedSignalProvider()
        assembler = StartupContextAssembler(
            resolver=MechanicalScopeResolver(registry),
            metadata_index=metadata_index,
            signal_providers=(provider,),
        )
        pack = assembler.assemble(startup_request())
        assert provider.calls == 0
        assert pack.signals == ()

    def test_a_broken_optional_provider_degrades_rather_than_destroys(
        self, registry: WorkspaceRegistry, metadata_index: InMemoryMetadataIndex
    ) -> None:
        # core/05 section 19: a failed optional subsystem must not take the
        # whole context pack down with it.
        working = RecordingSignalProvider()
        assembler = StartupContextAssembler(
            resolver=MechanicalScopeResolver(registry),
            metadata_index=metadata_index,
            signal_providers=(ExplodingSignalProvider(), working),
        )
        pack = assembler.assemble(startup_request())
        assert [signal.kind for signal in pack.signals] == ["git.branch"]
        assert pack.degraded_providers == ("exploding",)

    def test_startup_works_with_no_providers_at_all(
        self, assembler: StartupContextAssembler
    ) -> None:
        pack = assembler.assemble(startup_request())
        assert pack.signals == ()
        assert pack.items


class TestBoundedStartup:
    def test_only_the_highest_priority_body_survives_a_tight_document_budget(
        self,
        registry: WorkspaceRegistry,
        metadata_index: InMemoryMetadataIndex,
        document_store: InMemoryDocumentStore,
    ) -> None:
        assembler = StartupContextAssembler(
            resolver=MechanicalScopeResolver(registry),
            metadata_index=metadata_index,
            document_store=document_store,
        )
        pack = assembler.assemble(
            startup_request(
                scope=ScopeRequest(cwd=REPO / "runtime"),
                budget=ContextBudget(max_items=10, max_characters=10_000, max_full_documents=1),
            )
        )
        assert pack.usage.full_documents == 1
        assert [item.is_reference for item in pack.items] == [False, True]
        # The dropped body is still announced, not silently discarded.
        assert pack.items[1].concept_id == PARENT_ID
        assert pack.items[1].title == "Never4gA"

    def test_a_body_larger_than_the_whole_budget_becomes_a_reference(
        self,
        registry: WorkspaceRegistry,
        metadata_index: InMemoryMetadataIndex,
        document_store: InMemoryDocumentStore,
    ) -> None:
        assembler = StartupContextAssembler(
            resolver=MechanicalScopeResolver(registry),
            metadata_index=metadata_index,
            document_store=document_store,
        )
        pack = assembler.assemble(
            startup_request(budget=ContextBudget(max_items=10, max_characters=10))
        )
        assert pack.usage.full_documents == 0
        assert pack.usage.characters == 0
        assert all(item.is_reference for item in pack.items)

    def test_bodies_are_absent_without_a_document_store(
        self, registry: WorkspaceRegistry, metadata_index: InMemoryMetadataIndex
    ) -> None:
        assembler = StartupContextAssembler(
            resolver=MechanicalScopeResolver(registry),
            metadata_index=metadata_index,
        )
        pack = assembler.assemble(startup_request())
        assert all(item.body is None for item in pack.items)
        assert pack.items[0].title == "Never4gA"


class TestRequestCarriesNoModelConfiguration:
    def test_the_public_request_has_no_prompt_or_model_fields(self) -> None:
        # Context is requested with structure, never a prompt or a model.
        request = startup_request()
        for forbidden in (
            "prompt",
            "system_prompt",
            "instructions",
            "model",
            "temperature",
            "api_key",
            "provider",
        ):
            assert not hasattr(request, forbidden)

    def test_focused_retrieval_terms_are_structured_not_a_prompt(self) -> None:
        request = ContextRequest(
            scope=ScopeRequest(workspace_id=PARENT_ID),
            depth=ContextDepth.FOCUSED,
            budget=ContextBudget(max_items=5, max_characters=1000),
            terms=("workspace", "resolution"),
            exact_identifiers=("ADR-0002",),
        )
        assert request.terms == ("workspace", "resolution")
        assert request.exact_identifiers == ("ADR-0002",)

    def test_context_is_progressive(self) -> None:
        assert [depth.value for depth in ContextDepth] == ["startup", "focused", "deep"]

    def test_deeper_lanes_are_refused_rather_than_silently_downgraded(
        self, assembler: StartupContextAssembler
    ) -> None:
        # Each assembler builds one depth. Answering a focused or deep request
        # with a startup skeleton would under-deliver in silence, so this one
        # refuses both.
        for depth in (ContextDepth.FOCUSED, ContextDepth.DEEP):
            with pytest.raises(NotImplementedError, match="builds startup packs only"):
                assembler.assemble(startup_request(depth=depth))


class TestOneCategoryCannotStarveTheRest:
    """A pack that has lost a whole category is worse than one that is shorter.

    core/07 section 10. A workspace can hold many standards, such as a brand
    system, a bookkeeping cadence and a contract workflow. Per-category caps
    keep them from crowding every decision and log out of the pack.
    """

    @staticmethod
    def _crowded(index: InMemoryMetadataIndex) -> None:
        """Nineteen standards, ten decisions and two logs in one workspace."""
        for n, folder in enumerate(["Brand"] * 8 + ["Finance"] * 4 + ["Operations"] * 7):
            index.upsert(
                MetadataRecord(
                    concept_id=ConceptId.parse(f"0198d741-0cf9-7360-b426-9640810{n:05d}"),
                    concept_type="standard",
                    path=VaultPath.parse(f"10_Workspaces/never4ga/{folder}/s{n:02d}.md"),
                    title=f"{folder} standard {n}",
                    workspace_id=PARENT_ID,
                )
            )
        for n in range(10):
            index.upsert(
                MetadataRecord(
                    concept_id=ConceptId.parse(f"0198d741-0cf9-7360-b426-9640811{n:05d}"),
                    concept_type="decision",
                    path=VaultPath.parse(f"10_Workspaces/never4ga/Decisions/adr-{n:04d}.md"),
                    title=f"ADR-{n:04d}",
                    workspace_id=PARENT_ID,
                    lifecycle="accepted",
                )
            )
        for n in range(2):
            index.upsert(
                MetadataRecord(
                    concept_id=ConceptId.parse(f"0198d741-0cf9-7360-b426-9640812{n:05d}"),
                    concept_type="activity_log",
                    path=VaultPath.parse(f"10_Workspaces/never4ga/Logs/2026/2026-0{n + 7}.md"),
                    title=f"Hub Activity 2026-0{n + 7}",
                    workspace_id=PARENT_ID,
                )
            )

    def test_every_category_present_in_the_vault_reaches_the_pack(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        self._crowded(metadata_index)
        pack = assembler.assemble(startup_request(budget=startup_budget(max_items=25)))
        categories = {item.category for item in pack.items}
        assert "standard" in categories
        assert "decision" in categories, "standards crowded out every decision"
        assert "activity" in categories, "standards crowded out the logs"

    def test_the_standards_lane_spans_its_directories(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        # A cap alone is not enough. Ordered by path, a cap of six returns six
        # Brand documents and nothing from Finance or Operations -- the pack
        # would describe whichever folder sorts first.
        self._crowded(metadata_index)
        pack = assembler.assemble(startup_request(budget=startup_budget(max_items=25)))
        folders = {item.path.segments[-2] for item in pack.items if item.category == "standard"}
        assert folders == {"Brand", "Finance", "Operations"}

    def test_a_standards_rank_cannot_reach_the_decision_band(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        # Ordering standards by rank adds to their band. Clamped, so that a
        # workspace with hundreds of them cannot outrank its own decisions.
        for n in range(150):
            metadata_index.upsert(
                MetadataRecord(
                    concept_id=ConceptId.parse(f"0198d741-0cf9-7360-b426-9640813{n:05d}"),
                    concept_type="standard",
                    path=VaultPath.parse(f"10_Workspaces/never4ga/S{n:03d}/s.md"),
                    title=f"Standard {n}",
                    workspace_id=PARENT_ID,
                )
            )
        metadata_index.upsert(
            MetadataRecord(
                concept_id=ConceptId.parse("0198d741-0cf9-7360-b426-964081400001"),
                concept_type="decision",
                path=VaultPath.parse("10_Workspaces/never4ga/Decisions/adr-0001.md"),
                title="ADR-0001",
                workspace_id=PARENT_ID,
                lifecycle="accepted",
            )
        )
        pack = assembler.assemble(startup_request(budget=startup_budget(max_items=200)))
        standards = [i.priority for i in pack.items if i.category == "standard"]
        decisions = [i.priority for i in pack.items if i.category == "decision"]
        assert max(standards) < min(decisions)


class TestTheRequestSurvivesAssembly:
    """A field added to a request must reach the providers that read it.

    `ContextService.assemble` narrows the scope and passes the rest through.
    Rebuilding the request field by field would silently drop any field added
    later, far from where anyone would notice.
    """

    def test_every_field_reaches_the_provider(self) -> None:
        import dataclasses
        from pathlib import PurePath

        from never4ga.adapters.fakes import (
            FakeRepositoryLocator,
            InMemoryDocumentStore,
            InMemoryGraphIndex,
            InMemoryMetadataIndex,
            InMemoryTextIndex,
            InMemoryWorkspaceMappingStore,
        )
        from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
        from never4ga.domain.scope import ScopeRequest
        from never4ga.services.context import ContextService
        from never4ga.services.workspaces import WorkspaceService

        seen: list[ContextRequest] = []

        class Recording:
            provider_id = "recording"

            def supports(self, request: ContextRequest) -> bool:
                return True

            def collect(self, request: ContextRequest, scope: object) -> tuple[()]:
                seen.append(request)
                return ()

        workspace_id = ConceptId.new()
        workspaces = WorkspaceService(InMemoryWorkspaceMappingStore(), FakeRepositoryLocator())
        workspaces.map(
            workspace_id=workspace_id,
            workspace_path=VaultPath.parse("10_Workspaces/Example/workspace.md"),
            repository_root=PurePath("/repo"),
        )
        service = ContextService(
            workspaces=workspaces,
            metadata=InMemoryMetadataIndex(),
            text=InMemoryTextIndex(),
            graph=InMemoryGraphIndex(),
            documents=InMemoryDocumentStore(),
            signal_providers=(Recording(),),
        )
        request = ContextRequest(
            scope=ScopeRequest(workspace_id=workspace_id),
            depth=ContextDepth.FOCUSED,
            budget=ContextBudget(max_items=5, max_characters=5_000),
            terms=("mcp",),
            exact_identifiers=("ABC-1",),
            client="claude-code",
            task_given=True,
            work_item="838",
            refresh=True,
        )
        service.assemble(request)

        assert seen, "the provider was never asked"
        for field in dataclasses.fields(ContextRequest):
            if field.name == "scope":
                # The one field assembly is allowed to change: it narrows a
                # request's scope to the workspace that was resolved.
                continue
            assert getattr(seen[0], field.name) == getattr(request, field.name), field.name


class TestArchivedMaterialIsNotContext:
    """`90_Archive/` is detached, and detached has to mean detached.

    A retired standard is still shaped like a rule, so beside the standards in
    force it reads exactly like signal. The exclusion applies to every
    structural lane, not per category (core/07 section 3). Archived material
    is reachable by following a link or asking for it; it never arrives
    unasked.
    """

    def _archived_and_live(self, index: InMemoryMetadataIndex) -> None:
        for n, path in enumerate(
            (
                "50_System/Standards/live.md",
                "90_Archive/System/Standards/retired.md",
            )
        ):
            index.upsert(
                MetadataRecord(
                    concept_id=ConceptId.parse(f"0198d741-0cf9-7360-b426-96408140000{n}"),
                    concept_type="standard",
                    path=VaultPath.parse(path),
                    title=f"Standard {n}",
                )
            )

    def test_an_archived_standard_never_reaches_the_pack(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        self._archived_and_live(metadata_index)

        pack = assembler.assemble(startup_request())

        paths = {str(item.path) for item in pack.items}
        assert "50_System/Standards/live.md" in paths
        assert not any(path.startswith("90_Archive") for path in paths)

    def test_an_archived_log_never_reaches_the_pack(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        # A rolled-up year keeps pointing at the workspace whose history it is,
        # so an archived log matches a live workspace scope and would otherwise
        # arrive as "recent activity".
        metadata_index.upsert(
            MetadataRecord(
                concept_id=ConceptId.parse("0198d741-0cf9-7360-b426-964081400010"),
                concept_type="activity_log",
                path=VaultPath.parse("90_Archive/Workspaces/never4ga/Logs/2026/old.md"),
                title="Rolled-up year",
                workspace_id=PARENT_ID,
            )
        )

        pack = assembler.assemble(startup_request())

        assert not any(str(item.path).startswith("90_Archive") for item in pack.items)


class TestPlansAndGoalsReachThePack:
    """A workspace's plans and goals arrive structurally, not by luck.

    Without their own lanes, a plan would reach a session only if lexical
    retrieval happened to surface it, which needs a task. `core/07` section 9
    names active goal titles in the mechanical summary, beside accepted
    decision titles.

    Both lanes filter by lifecycle. A completed plan and an achieved goal are
    history: reachable by asking, not delivered unasked, on the same reasoning
    that keeps `90_Archive/` out of a pack.
    """

    def _planned(self, index: InMemoryMetadataIndex) -> None:
        for n, (kind, lifecycle) in enumerate(
            (
                ("plan", "active"),
                ("plan", "completed"),
                ("goal", "active"),
                ("goal", "achieved"),
            )
        ):
            index.upsert(
                MetadataRecord(
                    concept_id=ConceptId.parse(f"0198d741-0cf9-7360-b426-96408150000{n}"),
                    concept_type=kind,
                    path=VaultPath.parse(f"10_Workspaces/never4ga/{kind}-{n}.md"),
                    title=f"{kind} {lifecycle}",
                    workspace_id=PARENT_ID,
                    lifecycle=lifecycle,
                )
            )

    def test_an_active_plan_reaches_the_pack_with_no_task_given(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        self._planned(metadata_index)

        pack = assembler.assemble(startup_request())

        titles = {item.title for item in pack.items}
        assert "plan active" in titles

    def test_an_active_goal_reaches_the_pack(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        self._planned(metadata_index)

        pack = assembler.assemble(startup_request())

        assert "goal active" in {item.title for item in pack.items}

    def test_finished_work_is_not_delivered_unasked(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        self._planned(metadata_index)

        pack = assembler.assemble(startup_request())

        titles = {item.title for item in pack.items}
        assert "plan completed" not in titles
        assert "goal achieved" not in titles

    def test_they_carry_their_own_categories(
        self, assembler: StartupContextAssembler, metadata_index: InMemoryMetadataIndex
    ) -> None:
        # Labelling a plan as "retrieved" or "workspace" is a small lie an agent
        # cannot check, which is why PackCategory exists.
        self._planned(metadata_index)

        pack = assembler.assemble(startup_request())

        categories = {item.title: item.category for item in pack.items}
        assert categories["plan active"] == "plan"
        assert categories["goal active"] == "goal"
