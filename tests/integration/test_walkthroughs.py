"""A phase walkthrough: its type, the verb that starts one, and wrap's hold on it.

A walkthrough records how a plan's phase was actually done, step by step:
what, files, the exact commands, why, and how it was checked (core/02
section 21.27). A verb starts one for a phase, linked to its plan. The
session writes the steps, and `wrap` holds a declared walkthrough to having
changed, as it does a context document (core/04 section 37).
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from never4ga.adapters.fakes.session_store import InMemorySessionStore
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import StoredDocument
from never4ga.domain.identity import ConceptId
from never4ga.domain.sessions import Checkpoint, Session
from never4ga.schema import TYPE_REGISTRY, ValidationLevel, validate_document
from never4ga.services import ConceptCreationError
from never4ga.services.creation import ContentService
from never4ga.services.scaffold import template_body
from never4ga.services.vault import VaultInitializer
from never4ga.services.wrap import WrapService

START = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)
WORKSPACE_DIRECTORY = "10_Workspaces/Harbor"


def _clock() -> datetime:
    return datetime(2026, 8, 26, 18, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def documents(root: Path) -> FileSystemMarkdownStore:
    return FileSystemMarkdownStore(root)


@pytest.fixture
def content(root: Path, documents: FileSystemMarkdownStore) -> ContentService:
    files = FileSystemVaultFileStore(root)
    VaultInitializer(files, documents, now=_clock).initialize("Test Vault")
    documents.refresh()
    return ContentService(files, documents, now=_clock)


@pytest.fixture
def workspace(content: ContentService) -> ConceptId:
    return content.create_workspace("Harbor", workspace_type="product").concept_id


@pytest.fixture
def plan(content: ContentService, workspace: ConceptId) -> StoredDocument:
    return content.create_concept(
        "plan", "Harbor Build Plan", workspace=workspace, fields={"lifecycle": "active"}
    ).document


class TestTheType:
    def test_it_is_registered_with_its_own_home_and_lifecycle(self) -> None:
        spec = TYPE_REGISTRY["walkthrough"]
        assert spec.required_fields == ("workspace", "lifecycle")
        assert spec.lifecycle_values == ("not_started", "in_progress", "complete")
        assert "phase" in spec.extra_fields

    def test_its_template_carries_the_step_shape(self) -> None:
        body = template_body("walkthrough", "Phase 1")
        for heading in ("## Decided before starting", "## Steps", "## What is left"):
            assert heading in body
        for label in ("**What:**", "**Files:**", "**Commands:**", "**Why:**", "**Checked by:**"):
            assert label in body

    def test_the_plan_template_lists_its_phases(self) -> None:
        assert "## Phases" in template_body("plan", "Harbor Build Plan")


class TestStartingAPhase:
    def test_it_creates_the_walkthrough_in_the_plans_workspace(
        self, content: ContentService, plan: StoredDocument
    ) -> None:
        started = content.start_walkthrough(plan.concept_id, "3. The journey")
        assert str(started.path).startswith(f"{WORKSPACE_DIRECTORY}/Walkthroughs/")
        frontmatter = started.document.frontmatter
        assert frontmatter["type"] == "walkthrough"
        assert frontmatter["workspace"] == plan.frontmatter["workspace"]
        assert frontmatter["lifecycle"] == "in_progress"
        assert frontmatter["phase"] == "3. The journey"

    def test_it_links_to_its_plan(self, content: ContentService, plan: StoredDocument) -> None:
        started = content.start_walkthrough(plan.concept_id, "3. The journey")
        assert started.document.frontmatter["relations"] == [
            {"type": "implements", "target": str(plan.concept_id)}
        ]

    def test_its_title_names_the_plan_and_the_phase(
        self, content: ContentService, plan: StoredDocument
    ) -> None:
        started = content.start_walkthrough(plan.concept_id, "3. The journey")
        assert started.document.frontmatter["title"] == "Harbor Build Plan — 3. The journey"

    def test_it_is_valid(self, content: ContentService, plan: StoredDocument) -> None:
        started = content.start_walkthrough(plan.concept_id, "3. The journey")
        report = validate_document(
            started.path, started.document.frontmatter, level=ValidationLevel.STRICT
        )
        assert [issue.message for issue in report.issues] == []

    def test_the_plan_is_found_by_path_too(
        self, content: ContentService, plan: StoredDocument
    ) -> None:
        started = content.start_walkthrough(plan.path, "1. Foundation")
        assert started.document.frontmatter["phase"] == "1. Foundation"

    def test_it_says_how_the_plan_links_it(
        self, content: ContentService, plan: StoredDocument
    ) -> None:
        # The plan is hand-written, so the line is for the session to add
        # under the phase; it is relative to the plan's own folder.
        started = content.start_walkthrough(plan.concept_id, "3. The journey")
        title = "Harbor Build Plan — 3. The journey"
        assert started.plan_link == f"Walkthrough: [{title}](../Walkthroughs/{started.path.name})"

    def test_the_plan_is_not_touched(
        self, content: ContentService, plan: StoredDocument, root: Path
    ) -> None:
        before = (root / str(plan.path)).read_text(encoding="utf-8")
        content.start_walkthrough(plan.concept_id, "3. The journey")
        assert (root / str(plan.path)).read_text(encoding="utf-8") == before

    def test_only_a_plan_can_be_walked_through(
        self, content: ContentService, workspace: ConceptId
    ) -> None:
        note = content.create_concept("context", "Project State", workspace=workspace).document
        with pytest.raises(ConceptCreationError, match="plan"):
            content.start_walkthrough(note.concept_id, "1. Foundation")

    def test_an_unknown_plan_is_refused(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="no plan"):
            content.start_walkthrough(ConceptId.new(), "1. Foundation")

    def test_a_phase_already_started_is_refused(
        self, content: ContentService, plan: StoredDocument
    ) -> None:
        content.start_walkthrough(plan.concept_id, "3. The journey")
        with pytest.raises(ConceptCreationError, match="already exists"):
            content.start_walkthrough(plan.concept_id, "3. The journey")


class TestBackfill:
    def test_a_backfilled_phase_is_complete_and_says_it_was_reconstructed(
        self, content: ContentService, plan: StoredDocument
    ) -> None:
        done = content.start_walkthrough(plan.concept_id, "1. Foundation", backfill=True)
        assert done.document.frontmatter["lifecycle"] == "complete"
        assert "reconstructed after the fact" in done.document.body.lower()
        assert "[inferred]" in done.document.body

    def test_a_started_phase_does_not_say_so(
        self, content: ContentService, plan: StoredDocument
    ) -> None:
        started = content.start_walkthrough(plan.concept_id, "3. The journey")
        assert "reconstructed after the fact" not in started.document.body.lower()


# -- wrap holds a declared walkthrough to having changed ---------------------


@pytest.fixture
def sessions() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def wrap(
    sessions: InMemorySessionStore, content: ContentService, documents: FileSystemMarkdownStore
) -> WrapService:
    return WrapService(sessions, content, documents, now=_clock)


@pytest.fixture
def session(sessions: InMemorySessionStore, workspace: ConceptId) -> Session:
    opened = Session.opened(
        workspace=workspace, actor="claude-code/claude-opus-5", started_at=START
    )
    sessions.open(opened)
    return opened


@pytest.fixture
def walkthrough(content: ContentService, plan: StoredDocument, root: Path) -> StoredDocument:
    started = content.start_walkthrough(plan.concept_id, "3. The journey").document
    # Started before the session opened, as a phase usually is.
    before = (START - timedelta(days=2)).timestamp()
    os.utime(root / str(started.path), (before, before))
    return started


def declare(sessions: InMemorySessionStore, session: Session, *context: str) -> None:
    sessions.append(
        Checkpoint(
            session=session.id,
            recorded_at=START + timedelta(minutes=5),
            note="did a step",
            context=context,
        )
    )


class TestWrapHoldsAWalkthrough:
    def test_declared_and_unchanged_is_outstanding(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        walkthrough: StoredDocument,
    ) -> None:
        declare(sessions, session, f"{walkthrough.concept_id}: step 4, the deploy")
        assert wrap.wrap(session.id).outstanding_context == (str(walkthrough.path),)

    def test_declared_and_written_is_not(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        walkthrough: StoredDocument,
        documents: FileSystemMarkdownStore,
    ) -> None:
        current = documents.get(walkthrough.concept_id)
        assert current is not None
        documents.put(replace(current, body=current.body + "\n### 4. Deploy\n\n**What:** it\n"))
        declare(sessions, session, f"{walkthrough.path}: step 4, the deploy")
        assert wrap.wrap(session.id).outstanding_context == ()

    def test_the_log_lists_the_steps_apart_from_context_documents(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        walkthrough: StoredDocument,
        content: ContentService,
        workspace: ConceptId,
        documents: FileSystemMarkdownStore,
    ) -> None:
        state = content.create_concept("context", "Project State", workspace=workspace).document
        declare(
            sessions,
            session,
            f"{walkthrough.concept_id}: step 4, the deploy",
            f"{state.concept_id}: phase 3 is under way",
        )
        wrapped = wrap.wrap(session.id)
        log = documents.get(wrapped.concept_id)
        assert log is not None
        steps = log.body.split("## Walkthrough steps this session wrote", 1)[1]
        assert "step 4, the deploy" in steps.split("##", 1)[0]
        changed = log.body.split("## Context documents this session changed", 1)[1]
        assert "phase 3 is under way" in changed.split("##", 1)[0]
        assert "step 4, the deploy" not in changed.split("##", 1)[0]
