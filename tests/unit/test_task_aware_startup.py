"""Task-aware startup (core/04 section 16).

    never4ga context startup --client codex --task "Implement member search"

The specification shows the flag on `startup`, not on `focus`, so a task has to
do something at that depth or the flag is decoration. What it does is add a
short tail of what the task retrieved to the orientation the pack already
carries -- task-*aware*, not task-led. Structure still comes first, and without
a task nothing about the pack changes at all.
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
from never4ga.context.budget import startup_budget
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.context import (
    ContextDepth,
    ContextPack,
    ContextRequest,
    PackCategory,
    terms_from_task,
)
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.ports.text_index import IndexedChunk

WORKSPACE = ConceptId.new()
REPO = PurePosixPath("/home/user/Projects/never4ga")

RESEARCH = ConceptId.new()
UNRELATED = ConceptId.new()


@pytest.fixture
def metadata() -> InMemoryMetadataIndex:
    index = InMemoryMetadataIndex()
    for concept_id, concept_type, title, workspace, path in (
        (WORKSPACE, "workspace", "Never4gA", None, "10_Workspaces/n4g/workspace.md"),
        (RESEARCH, "research_note", "Member search", WORKSPACE, "10_Workspaces/n4g/Research/m.md"),
        (UNRELATED, "research_note", "Coffee", WORKSPACE, "10_Workspaces/n4g/Research/c.md"),
    ):
        index.upsert(
            MetadataRecord(
                concept_id=concept_id,
                concept_type=concept_type,
                path=VaultPath.parse(path),
                title=title,
                workspace_id=workspace,
            )
        )
    return index


@pytest.fixture
def text() -> InMemoryTextIndex:
    index = InMemoryTextIndex()
    for concept_id, body in (
        (RESEARCH, "how member search should rank a directory"),
        (UNRELATED, "coffee is unrelated to anything here"),
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
                path=VaultPath.parse("10_Workspaces/n4g/Research/x.md"),
                text=body,
                line_range=LineRange(start=1, end=1),
            )
        )
    return index


@pytest.fixture
def registry() -> WorkspaceRegistry:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=WORKSPACE,
            workspace_path=VaultPath.parse("10_Workspaces/n4g/workspace.md"),
            repository_root=REPO,
        )
    )
    return registry


@pytest.fixture
def assembler(
    registry: WorkspaceRegistry, metadata: InMemoryMetadataIndex, text: InMemoryTextIndex
) -> StartupContextAssembler:
    return StartupContextAssembler(
        resolver=MechanicalScopeResolver(registry),
        metadata_index=metadata,
        text_index=text,
        graph_index=InMemoryGraphIndex(),
    )


def startup(
    assembler: StartupContextAssembler, *, task: str | None = None, client: str | None = None
) -> ContextPack:
    return assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO / "src"),
            depth=ContextDepth.STARTUP,
            budget=startup_budget(),
            terms=terms_from_task(task or ""),
            client=client,
            task_given=task is not None,
        )
    )


def ids(pack: ContextPack) -> list[ConceptId | None]:
    return [item.concept_id for item in pack.items]


class TestATaskChangesThePack:
    def test_what_the_task_names_arrives(self, assembler: StartupContextAssembler) -> None:
        assert RESEARCH in ids(startup(assembler, task="Implement member search"))

    def test_what_it_does_not_name_stays_out(self, assembler: StartupContextAssembler) -> None:
        assert UNRELATED not in ids(startup(assembler, task="Implement member search"))

    def test_it_arrives_as_retrieved(self, assembler: StartupContextAssembler) -> None:
        pack = startup(assembler, task="Implement member search")
        found = next(item for item in pack.items if item.concept_id == RESEARCH)
        assert found.category is PackCategory.RETRIEVED

    def test_structure_still_leads(self, assembler: StartupContextAssembler) -> None:
        pack = startup(assembler, task="Implement member search")
        assert pack.items[0].concept_id == WORKSPACE


class TestNoTaskChangesNothing:
    def test_the_pack_is_structural(self, assembler: StartupContextAssembler) -> None:
        assert ids(startup(assembler)) == [WORKSPACE]

    def test_a_task_only_adds(self, assembler: StartupContextAssembler) -> None:
        without = ids(startup(assembler))
        with_task = ids(startup(assembler, task="Implement member search"))
        assert with_task[: len(without)] == without


class TestWhatTravelsOnward:
    def test_the_client_is_carried(self, assembler: StartupContextAssembler) -> None:
        assert startup(assembler, client="claude-code").client == "claude-code"

    def test_that_a_task_was_given_is_carried(self, assembler: StartupContextAssembler) -> None:
        assert startup(assembler, task="Implement member search").task_given is True

    def test_no_task_says_so(self, assembler: StartupContextAssembler) -> None:
        pack = startup(assembler)
        assert pack.task_given is False
        assert pack.client is None

    def test_startup_still_runs_no_model(self, assembler: StartupContextAssembler) -> None:
        # core/07 section 15 does not stop applying because a task was named.
        assert startup(assembler, task="Implement member search").llm_stages == ()
