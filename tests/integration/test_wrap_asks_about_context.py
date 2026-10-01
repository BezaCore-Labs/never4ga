"""`wrap` asks about the context documents a session was given (core/04 section 37).

A workspace's `Context/` documents reach every session, so a stale one misleads
every session until someone corrects it. The sessions doing the work are the
ones that know it is wrong, so they are asked.

So every wrap lists what the session's startup carried and asks whether the
session changed anything it asserts. A checkpoint may declare a context
document the session changed, and `wrap` holds it to that:

- a document the session was **given** is compared by the digest of its body
  when the session opened;
- one it was **not given** has no digest, so it is held to its modification
  time against the session's start -- the order of edit and declaration does
  not matter.

A declaration naming no context document is recorded and reported, never held.
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
from never4ga.domain.sessions import Checkpoint, GivenContext, Session, body_digest
from never4ga.services.creation import ContentService
from never4ga.services.vault import VaultInitializer
from never4ga.services.wrap import CONTEXT_QUESTION, WrapService

START = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)


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
    return content.create_workspace("Demo", workspace_type="product").concept_id


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


def context_document(
    content: ContentService, workspace: ConceptId, title: str, root: Path
) -> StoredDocument:
    made = content.create_concept("context", title, workspace=workspace)
    # Written before the session opened, as a real context document would be.
    before = (START - timedelta(days=3)).timestamp()
    os.utime(root / str(made.document.path), (before, before))
    return made.document


def give(sessions: InMemorySessionStore, session: Session, *given: StoredDocument) -> None:
    sessions.record_given_context(
        tuple(
            GivenContext(
                session=session.id,
                concept_id=document.concept_id,
                path=document.path,
                title=str(document.frontmatter["title"]),
                digest=body_digest(document.body),
            )
            for document in given
        )
    )


def checkpoint(
    sessions: InMemorySessionStore, session: Session, *context: str, note: str = "did work"
) -> None:
    sessions.append(
        Checkpoint(
            session=session.id,
            recorded_at=START + timedelta(minutes=5),
            note=note,
            context=context,
        )
    )


def rewrite_body(documents: FileSystemMarkdownStore, document: StoredDocument, body: str) -> None:
    current = documents.get(document.concept_id)
    assert current is not None
    documents.put(replace(current, body=body))


class TestEveryWrapAsks:
    def test_it_lists_what_the_session_was_given(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        state = context_document(content, workspace, "Project State", root)
        start = context_document(content, workspace, "Start Here", root)
        give(sessions, session, state, start)
        checkpoint(sessions, session)

        wrapped = wrap.wrap(session.id)

        assert [given.title for given in wrapped.context_given] == ["Project State", "Start Here"]
        assert wrapped.outstanding_context == ()

    def test_the_question_is_fixed(self) -> None:
        assert CONTEXT_QUESTION == "Did this session change anything these assert?"

    def test_a_session_given_nothing_is_asked_nothing(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        checkpoint(sessions, session)
        assert wrap.wrap(session.id).context_given == ()


class TestADocumentTheSessionWasGiven:
    def test_declared_and_unchanged_is_outstanding(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        state = context_document(content, workspace, "Project State", root)
        give(sessions, session, state)
        checkpoint(sessions, session, f"{state.concept_id}: the milestone closed")

        wrapped = wrap.wrap(session.id)

        assert wrapped.outstanding_context == (str(state.path),)
        assert wrapped.context == (f"{state.concept_id}: the milestone closed",)

    def test_declared_and_rewritten_is_not(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        documents: FileSystemMarkdownStore,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        state = context_document(content, workspace, "Project State", root)
        give(sessions, session, state)
        rewrite_body(documents, state, "# Project State\n\nMilestone 13 closed.\n")
        checkpoint(sessions, session, f"{state.concept_id}: the milestone closed")

        assert wrap.wrap(session.id).outstanding_context == ()

    def test_it_can_be_named_by_its_path(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        state = context_document(content, workspace, "Project State", root)
        give(sessions, session, state)
        checkpoint(sessions, session, f"{state.path}: the milestone closed")

        assert wrap.wrap(session.id).outstanding_context == (str(state.path),)

    def test_a_frontmatter_edit_is_not_a_correction(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        documents: FileSystemMarkdownStore,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        # Pushing stale_after forward says nothing new about what it asserts.
        state = context_document(content, workspace, "Project State", root)
        give(sessions, session, state)
        current = documents.get(state.concept_id)
        assert current is not None
        documents.put(
            replace(
                current,
                frontmatter={**current.frontmatter, "stale_after": "2027-01-01T00:00:00Z"},
            )
        )
        checkpoint(sessions, session, str(state.concept_id))

        assert wrap.wrap(session.id).outstanding_context == (str(state.path),)

    def test_declared_twice_is_reported_once(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        state = context_document(content, workspace, "Project State", root)
        give(sessions, session, state)
        checkpoint(sessions, session, str(state.concept_id), str(state.path))

        assert wrap.wrap(session.id).outstanding_context == (str(state.path),)


class TestADocumentTheSessionWasNotGiven:
    def test_modified_after_the_session_opened_is_updated(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        documents: FileSystemMarkdownStore,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        # Edit first, declare after: the natural order, and the one a digest
        # taken at declaration time would have flagged.
        parent = context_document(content, workspace, "Agent Context", root)
        rewrite_body(documents, parent, "# Agent Context\n\nThe rebrand is not done.\n")
        checkpoint(sessions, session, f"{parent.concept_id}: rebrand status")

        assert wrap.wrap(session.id).outstanding_context == ()

    def test_untouched_since_before_the_session_is_outstanding(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        parent = context_document(content, workspace, "Agent Context", root)
        checkpoint(sessions, session, f"{parent.concept_id}: rebrand status")

        assert wrap.wrap(session.id).outstanding_context == (str(parent.path),)


class TestWhatIsNeverHeld:
    def test_a_declaration_naming_no_context_document_is_reported_only(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
    ) -> None:
        note = content.create_concept("decision", "Something Decided", workspace=workspace)
        checkpoint(
            sessions,
            session,
            f"{note.concept_id}: a decision is not a context document",
            "the charter probably wants a look",
            "10_Workspaces/Nowhere/Context/missing.md",
        )

        wrapped = wrap.wrap(session.id)

        assert wrapped.outstanding_context == ()
        assert len(wrapped.context) == 3


class TestTheLog:
    def test_it_records_what_was_declared(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        state = context_document(content, workspace, "Project State", root)
        checkpoint(sessions, session, f"{state.path}: the milestone closed")

        body = wrap.wrap(session.id).document.body

        assert "## Context documents this session changed" in body
        assert f"- {state.path}: the milestone closed" in body

    def test_a_rewrite_holds_the_session_to_the_same_account(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
        root: Path,
    ) -> None:
        state = context_document(content, workspace, "Project State", root)
        give(sessions, session, state)
        checkpoint(sessions, session, str(state.concept_id))
        wrap.wrap(session.id)

        again = wrap.wrap(session.id)

        assert again.updated
        assert again.outstanding_context == (str(state.path),)
        assert [given.title for given in again.context_given] == ["Project State"]
