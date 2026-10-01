"""A course workspace reaches the life area that holds it.

A course lives under `20_Life/<Area>/Workspaces/` with the area as its `parent`
(`core/03` §30), and `area` is the field on the documents the area itself owns
(`core/02` §16.3). The scope resolver carries the area's id in a course's
parent chain. These tests pin that the id alone brings the area's documents
into focused and deep context, and that startup still leaves them out, because
an area is a parent and startup omits a parent's sections.
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
from never4ga.context.focused import FocusedContextAssembler
from never4ga.context.scope import MechanicalScopeResolver, WorkspaceRegistry
from never4ga.domain.chunk import ChunkIdentity, LineRange
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import ScopeRequest, WorkspaceMapping
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.ports.text_index import IndexedChunk

AREA = ConceptId.new()
COURSE = ConceptId.new()
OTHER_AREA = ConceptId.new()
AREA_DECISION = ConceptId.new()
OTHER_AREAS_DECISION = ConceptId.new()
REPO = PurePosixPath("/home/user/Projects/cs-201")


@pytest.fixture
def registry() -> WorkspaceRegistry:
    # The registry holds workspaces only; an area is never a mapping. The
    # course names the area as its parent, which is all the chain has to go on.
    registry = WorkspaceRegistry()
    registry.register(
        WorkspaceMapping(
            workspace_id=COURSE,
            workspace_path=VaultPath.parse("20_Life/Education/Workspaces/Northfield/workspace.md"),
            repository_root=REPO,
            parent_id=AREA,
        )
    )
    return registry


@pytest.fixture
def metadata() -> InMemoryMetadataIndex:
    index = InMemoryMetadataIndex()
    for concept_id, concept_type, title, path, owner in (
        (AREA, "life_area", "Education", "20_Life/Education/area.md", None),
        (
            COURSE,
            "workspace",
            "Northfield",
            "20_Life/Education/Workspaces/Northfield/workspace.md",
            None,
        ),
        # What the projection makes of a decision carrying `area: AREA`.
        (
            AREA_DECISION,
            "decision",
            "programs and courses are workspaces",
            "20_Life/Education/Decisions/programs-and-courses-are-workspaces.md",
            AREA,
        ),
        # Another area's ruling, indexed with the same words, so nothing but
        # its owner can be what keeps it out.
        (
            OTHER_AREAS_DECISION,
            "decision",
            "somebody else's ruling",
            "20_Life/Health/Decisions/somebody-elses-ruling.md",
            OTHER_AREA,
        ),
    ):
        index.upsert(
            MetadataRecord(
                concept_id=concept_id,
                concept_type=concept_type,
                path=VaultPath.parse(path),
                title=title,
                workspace_id=owner,
                lifecycle="accepted" if concept_type == "decision" else None,
            )
        )
    return index


@pytest.fixture
def text() -> InMemoryTextIndex:
    index = InMemoryTextIndex()
    for concept_id in (AREA_DECISION, OTHER_AREAS_DECISION):
        index.index_chunk(
            IndexedChunk(
                chunk=ChunkIdentity(
                    concept_id=concept_id,
                    heading_path=(),
                    ordinal=0,
                    content_hash="abc123",
                    policy_version="v1",
                ),
                path=VaultPath.parse("20_Life/thing.md"),
                text="one workspace or one per course: programs and courses are workspaces",
                line_range=LineRange(start=1, end=1),
            )
        )
    return index


@pytest.fixture
def focused(
    registry: WorkspaceRegistry, metadata: InMemoryMetadataIndex, text: InMemoryTextIndex
) -> FocusedContextAssembler:
    return FocusedContextAssembler(
        resolver=MechanicalScopeResolver(registry),
        metadata_index=metadata,
        text_index=text,
        graph_index=InMemoryGraphIndex(),
    )


@pytest.fixture
def startup(
    registry: WorkspaceRegistry, metadata: InMemoryMetadataIndex
) -> StartupContextAssembler:
    return StartupContextAssembler(
        resolver=MechanicalScopeResolver(registry), metadata_index=metadata
    )


def _ids(
    assembler: FocusedContextAssembler | StartupContextAssembler, depth: ContextDepth
) -> list[ConceptId | None]:
    assembled = assembler.assemble(
        ContextRequest(
            scope=ScopeRequest(cwd=REPO / "unit-1"),
            depth=depth,
            budget=ContextBudget(max_items=50, max_characters=100_000),
            terms=("workspace", "course"),
            task_given=True,
        )
    )
    return [item.concept_id for item in assembled.items]


class TestAnAreaOwnsWhatItHolds:
    def test_the_areas_ruling_reaches_a_course_under_it(
        self, focused: FocusedContextAssembler
    ) -> None:
        assert AREA_DECISION in _ids(focused, ContextDepth.FOCUSED)

    def test_another_areas_ruling_does_not(self, focused: FocusedContextAssembler) -> None:
        assert OTHER_AREAS_DECISION not in _ids(focused, ContextDepth.FOCUSED)

    def test_startup_still_leaves_a_parents_sections_out(
        self, startup: StartupContextAssembler
    ) -> None:
        # Not widened here: a session opening in a course needs to know the
        # area exists far more than it needs the area's decision log, exactly
        # as with a parent workspace. Focused and deep are where it arrives.
        assert AREA_DECISION not in _ids(startup, ContextDepth.STARTUP)
