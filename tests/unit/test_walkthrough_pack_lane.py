"""The startup pack names the walkthrough of each phase in progress.

A walkthrough is the step-by-step record of a plan's phase (core/02 section
21.27). The one in progress is what a session is most likely there to
continue, so every startup names it and says why, and carries it as a
reference to be read on demand, because one can run to tens of thousands of
characters.
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
    ContextItem,
    ContextPack,
    ContextRequest,
    PackCategory,
)
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.metadata_index import MetadataRecord

WORKSPACE = ConceptId.new()
OTHER = ConceptId.new()
REPO = PurePosixPath("/home/alex/Projects/harbor")


def walkthrough(
    title: str, lifecycle: str, *, workspace_id: ConceptId = WORKSPACE, **extra: object
) -> MetadataRecord:
    return MetadataRecord(
        concept_id=ConceptId.new(),
        concept_type="walkthrough",
        path=VaultPath.parse(f"10_Workspaces/Harbor/Walkthroughs/{title}.md"),
        title=title,
        workspace_id=workspace_id,
        lifecycle=lifecycle,
        extra=extra,
    )


@pytest.fixture
def assembler() -> StartupContextAssembler:
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=WORKSPACE,
            workspace_path=VaultPath.parse("10_Workspaces/Harbor/workspace.md"),
            repository_root=REPO,
        )
    )
    registry.register(
        WorkspaceMapping(
            workspace_id=OTHER,
            workspace_path=VaultPath.parse("10_Workspaces/Lighthouse/workspace.md"),
        )
    )
    index = InMemoryMetadataIndex()
    for entry in (
        MetadataRecord(
            concept_id=WORKSPACE,
            concept_type="workspace",
            path=VaultPath.parse("10_Workspaces/Harbor/workspace.md"),
            title="Harbor",
        ),
        walkthrough("phase-3", "in_progress", phase="3. The journey"),
        walkthrough("phase-2", "complete", phase="2. The plain site"),
        walkthrough("phase-4", "not_started", phase="4. The API"),
        walkthrough("elsewhere", "in_progress", workspace_id=OTHER),
    ):
        index.upsert(entry)
    return StartupContextAssembler(resolver=MechanicalScopeResolver(registry), metadata_index=index)


def pack(assembler: StartupContextAssembler) -> ContextPack:
    return assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO),
            depth=ContextDepth.STARTUP,
            budget=ContextBudget(max_items=50, max_characters=100_000),
        )
    )


def walkthroughs(assembled: ContextPack) -> list[ContextItem]:
    return [item for item in assembled.items if item.category == PackCategory.WALKTHROUGH]


def test_only_the_phase_in_progress_is_named(assembler: StartupContextAssembler) -> None:
    assert [item.title for item in walkthroughs(pack(assembler))] == ["phase-3"]


def test_it_says_why_it_is_there(assembler: StartupContextAssembler) -> None:
    (item,) = walkthroughs(pack(assembler))
    assert item.reason.detail == "current phase walkthrough, phase 3. The journey"


def test_it_is_a_reference_and_not_required_reading(assembler: StartupContextAssembler) -> None:
    (item,) = walkthroughs(pack(assembler))
    assert item.is_reference
    assert item.body is None
    assert not item.required
