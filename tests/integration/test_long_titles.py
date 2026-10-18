"""A title too long for a filename still makes a document.

A title has no length limit, and a filename does: common filesystems refuse a
name over 255 bytes, and the atomic write needs room beside it for a
temporary name. The name is capped at a whole word; the title keeps every
word in frontmatter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from never4ga.adapters.fakes.session_store import InMemorySessionStore
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.identity import ConceptId
from never4ga.domain.sessions import Checkpoint, Session
from never4ga.services.creation import ContentService
from never4ga.services.vault import VaultInitializer
from never4ga.services.wrap import WrapService

START = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)

LONG = (
    "The demo site redesigned and landed, with every page on one shared kit and a graphic "
    "of its own, a header with menus, prices shown as typical ranges with one price, a "
    "working estimator, and every check green before the merge, after a week of reviews on "
    "a phone and a desktop and three rounds of changes to how the prices read"
)


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
    return ContentService(files, documents, now=_clock)


@pytest.fixture
def workspace(content: ContentService) -> ConceptId:
    return content.create_workspace("Demo", workspace_type="product").concept_id


def test_a_concept_with_a_very_long_title_is_written(
    content: ContentService, workspace: ConceptId, root: Path
) -> None:
    made = content.create_concept("context", LONG, workspace=workspace)

    assert (root / str(made.document.path)).is_file()
    assert len(made.document.path.segments[-1].encode("utf-8")) < 255
    assert made.document.frontmatter["title"] == LONG


def test_a_session_wrapped_with_a_very_long_title_writes_its_log(
    content: ContentService, documents: FileSystemMarkdownStore, workspace: ConceptId, root: Path
) -> None:
    sessions = InMemorySessionStore()
    session = Session.opened(
        workspace=workspace, actor="claude-code/claude-opus-5", started_at=START
    )
    sessions.open(session)
    sessions.append(
        Checkpoint(session=session.id, recorded_at=START + timedelta(minutes=5), note="did work")
    )

    wrapped = WrapService(sessions, content, documents, now=_clock).wrap(session.id, title=LONG)

    assert (root / str(wrapped.document.path)).is_file()
    assert wrapped.document.path.segments[-1].startswith("2026-08-26_the-demo-site-redesigned")
    assert wrapped.document.frontmatter["title"] == LONG
