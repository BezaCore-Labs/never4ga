"""`wrap` writes the one record a session leaves behind.

The rule is core/04 section 37.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from never4ga.adapters.fakes.session_store import InMemorySessionStore
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import Checkpoint, Session
from never4ga.errors import Never4gaError
from never4ga.services.creation import ContentService
from never4ga.services.vault import VaultInitializer
from never4ga.services.wrap import WorkspaceGoneError, WrapService

START = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)


def _clock() -> datetime:
    return datetime(2026, 8, 26, 18, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
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
    sessions: InMemorySessionStore,
    content: ContentService,
    documents: FileSystemMarkdownStore,
) -> WrapService:
    return WrapService(sessions, content, documents, now=_clock)


@pytest.fixture
def session(sessions: InMemorySessionStore, workspace: ConceptId) -> Session:
    opened = Session.opened(
        workspace=workspace,
        actor="claude-code/claude-opus-5",
        started_at=START,
        client="claude-code",
    )
    sessions.open(opened)
    return opened


def _checkpoint(
    sessions: InMemorySessionStore,
    session: Session,
    note: str,
    *,
    minutes: int = 0,
    actions: tuple[str, ...] = (),
    decisions: tuple[str, ...] = (),
    memories: tuple[str, ...] = (),
) -> None:
    sessions.append(
        Checkpoint(
            session=session.id,
            recorded_at=START + timedelta(minutes=minutes),
            note=note,
            actions=actions,
            decisions=decisions,
            memories=memories,
        )
    )


class TestWhereTheLogLands:
    def test_it_lands_in_the_year_folder_with_a_date_prefix(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session, root: Path
    ) -> None:
        _checkpoint(sessions, session, "Cleared two defects")
        wrapped = wrap.wrap(session.id)
        assert str(wrapped.document.path).endswith("/Logs/2026/2026-08-26_cleared-two-defects.md")
        assert (root / str(wrapped.document.path)).is_file()

    def test_it_is_dated_from_the_session_not_from_the_wrap(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # The session ran at 09:30; the clock says 18:00. A log names the day
        # it is about, the way a handoff written on Monday about Friday does.
        _checkpoint(sessions, session, "Worked in the morning")
        wrapped = wrap.wrap(session.id)
        assert wrapped.document.frontmatter["occurred_at"] == START.isoformat()

    def test_the_actor_survives(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # A log an agent wrote must not claim a human wrote it (core/02
        # section 5.2).
        _checkpoint(sessions, session, "Did the thing")
        wrapped = wrap.wrap(session.id)
        assert wrapped.document.frontmatter["generated"]["by"] == "claude-code/claude-opus-5"

    def test_it_belongs_to_the_sessions_workspace(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        workspace: ConceptId,
    ) -> None:
        _checkpoint(sessions, session, "Did the thing")
        wrapped = wrap.wrap(session.id)
        assert wrapped.document.frontmatter["workspace"] == str(workspace)


class TestWhatTheLogSays:
    def test_every_checkpoint_appears_in_order(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        for index in range(3):
            _checkpoint(sessions, session, f"step {index}", minutes=index * 10)
        body = wrap.wrap(session.id).document.body
        assert body.index("step 0") < body.index("step 1") < body.index("step 2")

    def test_actions_are_listed_apart_from_the_narrative(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "Merged the fix", actions=("merged #40",))
        body = wrap.wrap(session.id).document.body
        assert "## Actions" in body
        assert "- merged #40" in body

    def test_it_does_not_invent_decisions(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # A `wrap` that filled in "Decided" and "Next" from a checkpoint note
        # would be making a judgement that belongs to the agent. The agent
        # declares; Never4gA records.
        _checkpoint(sessions, session, "Did the thing")
        body = wrap.wrap(session.id).document.body
        assert "## Decided" not in body
        assert "## Next" not in body


class TestTheHeadingMatchesTheTitle:
    def test_a_given_title_is_the_heading(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # The heading comes from the title, not the first checkpoint, so a
        # document never contradicts its own frontmatter.
        _checkpoint(sessions, session, "some rambling note")
        wrapped = wrap.wrap(session.id, title="Milestone 7 Wrap")
        assert wrapped.document.frontmatter["title"] == "Milestone 7 Wrap"
        assert wrapped.document.body.startswith("# Milestone 7 Wrap\n")

    def test_a_rewrite_with_no_title_keeps_the_documents_own(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # Silence is not a retitle. Without a `--title` the document keeps what
        # it has rather than re-deriving from the first checkpoint, which would
        # change a chosen title behind the caller's back.
        _checkpoint(sessions, session, "first")
        wrap.wrap(session.id, title="Chosen Title")
        _checkpoint(sessions, session, "second", minutes=10)
        rewritten = wrap.wrap(session.id)
        assert rewritten.document.frontmatter["title"] == "Chosen Title"
        assert rewritten.document.body.startswith("# Chosen Title\n")
        assert rewritten.renamed_from is None

    def test_an_explicit_title_on_a_rewrite_replaces_the_old_one(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # A session wrapped early is titled for what had happened so far, and
        # the point of wrapping again is that more happened, often enough that
        # the later work changes what the session was about. Dropping the flag
        # would leave a title that contradicts the checkpoints beneath it.
        _checkpoint(sessions, session, "first")
        wrap.wrap(session.id, title="A Collision That Needs A Ruling")
        _checkpoint(sessions, session, "second", minutes=10)
        rewritten = wrap.wrap(session.id, title="Dissolved The Type Instead")
        assert rewritten.document.frontmatter["title"] == "Dissolved The Type Instead"
        assert rewritten.document.body.startswith("# Dissolved The Type Instead\n")


class TestWrappingTwice:
    def test_it_updates_one_document_rather_than_writing_two(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session, root: Path
    ) -> None:
        _checkpoint(sessions, session, "First pass")
        first = wrap.wrap(session.id)
        _checkpoint(sessions, session, "Second pass", minutes=30)
        second = wrap.wrap(session.id)

        assert second.updated is True
        assert second.concept_id == first.concept_id
        assert second.document.path == first.document.path
        # `index.md` is navigation rather than a log, and it is written when
        # the log lands, so counting every `.md` in the directory would count
        # it.
        logs = [
            path
            for path in (root / str(first.document.path)).parent.glob("*.md")
            if path.name != "index.md"
        ]
        assert len(logs) == 1

    def test_the_second_wrap_carries_the_later_checkpoints(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "First pass")
        wrap.wrap(session.id)
        _checkpoint(sessions, session, "Second pass", minutes=30)
        assert "Second pass" in wrap.wrap(session.id).document.body

    def test_the_first_wrap_is_not_marked_as_an_update(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "Only pass")
        assert wrap.wrap(session.id).updated is False

    def test_a_session_can_carry_on_after_being_wrapped(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # Wrapping early should not be a mistake a person cannot undo.
        _checkpoint(sessions, session, "Thought I was done")
        wrap.wrap(session.id)
        _checkpoint(sessions, session, "One more thing", minutes=60)
        assert "One more thing" in wrap.wrap(session.id).document.body


class TestRefusals:
    def test_a_session_with_no_checkpoints_is_refused(
        self, wrap: WrapService, session: Session
    ) -> None:
        # An empty log claims a session happened and says nothing about it.
        with pytest.raises(Never4gaError, match="no checkpoints"):
            wrap.wrap(session.id)

    def test_an_unknown_session_is_refused(self, wrap: WrapService) -> None:
        with pytest.raises(Never4gaError, match="never opened"):
            wrap.wrap(SessionId.new())

    def test_a_log_deleted_outside_never4ga_is_reported_not_recreated(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        documents: FileSystemMarkdownStore,
        root: Path,
    ) -> None:
        # Never4gA is not the only writer of the Markdown. If the log is gone,
        # say so. Silently writing a second one would hide the deletion.
        _checkpoint(sessions, session, "Did the thing")
        wrapped = wrap.wrap(session.id)
        (root / str(wrapped.document.path)).unlink()
        documents.refresh()
        with pytest.raises(Never4gaError, match="no longer in the vault"):
            wrap.wrap(session.id)


class TestWhatTheAgentDeclared:
    """The agent declares, Never4gA records.

    Never4gA cannot generate a proposal: it makes no LLM call here, so it has
    only notes, actions and an identity, none of which is judgement. The agent
    holds the conversation, so the agent declares.
    """

    def test_a_declared_decision_reaches_the_log(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(
            sessions, session, "scoped it", decisions=("session ids are minted by Never4gA",)
        )
        body = wrap.wrap(session.id).document.body
        assert "## Decided" in body
        assert "- session ids are minted by Never4gA" in body

    def test_a_declaration_is_recorded_as_claimed(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # Recording is not interpreting. A store or a service that reworded a
        # declaration would be having an opinion about it.
        exact = "we chose SQLite over JSON, badly phrased and all"
        _checkpoint(sessions, session, "did a thing", decisions=(exact,))
        assert f"- {exact}" in wrap.wrap(session.id).document.body

    def test_the_log_says_a_declaration_is_not_an_accepted_decision(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # A decision in a log is a claim about a session. Only an ADR is a
        # decision in force, and the log has to say so or it reads like one.
        _checkpoint(sessions, session, "did a thing", decisions=("something",))
        assert "until somebody writes the ADR" in wrap.wrap(session.id).document.body

    def test_things_worth_remembering_get_their_own_heading(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(
            sessions, session, "did a thing", memories=("Actions lags by about fifteen minutes",)
        )
        body = wrap.wrap(session.id).document.body
        assert "## Worth remembering" in body
        assert "- Actions lags by about fifteen minutes" in body

    def test_declaring_nothing_produces_no_headings(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "just worked")
        body = wrap.wrap(session.id).document.body
        assert "## Decided" not in body
        assert "## Worth remembering" not in body

    def test_they_are_handed_back_in_order(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "first", decisions=("one",), minutes=0)
        _checkpoint(sessions, session, "second", decisions=("two", "three"), minutes=10)
        wrapped = wrap.wrap(session.id)
        assert wrapped.decisions == ("one", "two", "three")

    def test_nothing_is_written_outside_the_log(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session, root: Path
    ) -> None:
        # The refusal that matters most: a proposal must not land in Decisions/.
        _checkpoint(sessions, session, "did a thing", decisions=("a big ruling",))
        wrapped = wrap.wrap(session.id)
        written = {
            str(path.relative_to(root))
            for path in root.rglob("*.md")
            if path.stat().st_mtime >= (root / str(wrapped.document.path)).stat().st_mtime - 1
        }
        assert not any("Decisions/" in path for path in written)


class TestRetitlingMovesTheFile:
    """A retitle moves the file too, not only the title.

    A path naming the old subject is as wrong as a title naming it. `core/06`
    section 3 makes identity independent of path, so the move is safe for
    identity. A dated log is normally linked only from its year index, which is
    generated navigation that moves with it.
    """

    def test_the_file_moves_to_match_the_new_title(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        root: Path,
    ) -> None:
        _checkpoint(sessions, session, "first")
        first = wrap.wrap(session.id, title="A Collision That Needs A Ruling")
        _checkpoint(sessions, session, "second", minutes=10)
        second = wrap.wrap(session.id, title="Dissolved The Type Instead")

        assert str(second.document.path).endswith(
            "/Logs/2026/2026-08-26_dissolved-the-type-instead.md"
        )
        assert (root / str(second.document.path)).is_file()
        assert not (root / str(first.document.path)).exists()

    def test_the_date_prefix_is_the_sessions_not_the_wraps(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # The session ran on the 26th; the clock says 18:00 the same day, but
        # the rule that put the log under its own date must not be re-decided
        # by a rename. Only the subject half of the filename moves.
        _checkpoint(sessions, session, "first")
        wrap.wrap(session.id, title="Early")
        _checkpoint(sessions, session, "second", minutes=10)
        renamed = wrap.wrap(session.id, title="Late")
        assert renamed.document.path.name.startswith("2026-08-26_")

    def test_the_identity_survives_the_move(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "first")
        first = wrap.wrap(session.id, title="Early")
        _checkpoint(sessions, session, "second", minutes=10)
        second = wrap.wrap(session.id, title="Late")
        assert second.concept_id == first.concept_id
        assert second.updated is True

    def test_it_moves_rather_than_leaving_two_logs(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        root: Path,
    ) -> None:
        _checkpoint(sessions, session, "first")
        first = wrap.wrap(session.id, title="Early")
        _checkpoint(sessions, session, "second", minutes=10)
        wrap.wrap(session.id, title="Late")
        logs = [
            path
            for path in (root / str(first.document.path)).parent.glob("*.md")
            if path.name != "index.md"
        ]
        assert len(logs) == 1

    def test_the_move_is_reported_rather_than_silent(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        # A rename nobody is told about is how a link goes stale without
        # anybody choosing that. The caller is handed where the file was.
        _checkpoint(sessions, session, "first")
        first = wrap.wrap(session.id, title="Early")
        _checkpoint(sessions, session, "second", minutes=10)
        second = wrap.wrap(session.id, title="Late")
        assert second.renamed_from == first.document.path

    def test_the_same_title_again_is_not_a_move(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "first")
        first = wrap.wrap(session.id, title="Chosen Title")
        _checkpoint(sessions, session, "second", minutes=10)
        second = wrap.wrap(session.id, title="Chosen Title")
        assert second.document.path == first.document.path
        assert second.renamed_from is None

    def test_navigation_follows_the_file(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        root: Path,
    ) -> None:
        # The year index names its logs by path. Moving the file without it
        # would leave a `broken_link` finding against a directory `wrap` had
        # just touched.
        _checkpoint(sessions, session, "first")
        first = wrap.wrap(session.id, title="Early")
        _checkpoint(sessions, session, "second", minutes=10)
        second = wrap.wrap(session.id, title="Late")

        index = (root / str(second.document.path)).parent / "index.md"
        listing = index.read_text()
        assert second.document.path.name in listing
        assert first.document.path.name not in listing


class TestProseAboutALinkIsNotALink:
    """A checkpoint note mentioning `[[...]]` does not write a real wikilink.

    `wrap` stores a note verbatim and renders it into the log. A session that
    wrote *about* link syntax would otherwise put a live wikilink into a
    document Never4gA generated itself, and `doctor` would report `broken_link`
    against it. Editing the log would not help: `wrap` is idempotent and
    re-renders from the stored checkpoints, so the next wrap would restore the
    brackets.

    Link checking follows link *targets* and not bare code spans: a
    `[text](path)` is an author saying this is somewhere to go, and a
    backticked path may be prose *about* a path. So a link-shaped run in a
    note becomes a code span, the form link checking already treats as prose.

    A checkpoint note is the one place in the log that is somebody's own
    words. Everything `wrap` builds around it (the work items, the decisions)
    it writes itself, as Markdown links. Nothing authors vault navigation
    inside a free-text note, so nothing is lost by treating the whole of it as
    prose.
    """

    def test_a_note_mentioning_wikilink_syntax_writes_no_link(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "the resolver reads [[Note]] and [[folder/Note]]")

        body = wrap.wrap(session.id).document.body

        assert "[[Note]]" not in body.replace("`[[Note]]`", "")
        assert "`[[Note]]`" in body
        assert "`[[folder/Note]]`" in body

    def test_the_words_survive(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        """Verbatim is the point of a checkpoint; only the syntax is defused."""
        _checkpoint(sessions, session, "the resolver reads [[Note]] and [[folder/Note]]")

        body = wrap.wrap(session.id).document.body

        assert "the resolver reads" in body
        assert "folder/Note" in body

    def test_a_note_already_using_a_code_span_is_left_alone(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        """Double-wrapping would render as literal backticks."""
        _checkpoint(sessions, session, "the resolver reads `[[Note]]`")

        body = wrap.wrap(session.id).document.body

        assert "``[[Note]]``" not in body
        assert "`[[Note]]`" in body

    def test_an_ordinary_note_is_untouched(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        """The control: this must not start backticking prose at large."""
        _checkpoint(sessions, session, "landed the indexer and closed 1107")

        body = wrap.wrap(session.id).document.body

        assert "landed the indexer and closed 1107" in body
        assert "`" not in body.split("## What happened")[1].splitlines()[2]

    def test_it_survives_wrapping_twice(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        """A second wrap re-renders from the checkpoints and must not restore them."""
        _checkpoint(sessions, session, "the resolver reads [[Note]]")

        wrap.wrap(session.id)
        body = wrap.wrap(session.id).document.body

        assert "``" not in body
        assert "`[[Note]]`" in body


class TestWhenTheWorkspaceIsGone:
    """A session that restructures its workspace out of existence can still close.

    The log belongs in the archive, or in the workspace the original migrated
    to.
    """

    def _gone(self, root: Path, documents: FileSystemMarkdownStore) -> None:
        shutil.rmtree(root / "10_Workspaces" / "Demo")
        documents.refresh()

    def test_it_is_refused_with_a_way_forward(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        root: Path,
        documents: FileSystemMarkdownStore,
    ) -> None:
        _checkpoint(sessions, session, "Moved Demo under an area and deleted it")
        self._gone(root, documents)
        with pytest.raises(WorkspaceGoneError) as caught:
            wrap.wrap(session.id)
        # The error says what happened and what to pass, not only a uuid.
        assert "no longer in the vault" in str(caught.value)
        assert "--into" in str(caught.value)

    def test_the_log_can_be_directed_into_the_workspace_it_became(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        root: Path,
        documents: FileSystemMarkdownStore,
    ) -> None:
        _checkpoint(sessions, session, "Merged Demo into Demo Two")
        successor = content.create_workspace("Demo Two", workspace_type="product").concept_id
        self._gone(root, documents)
        wrapped = wrap.wrap(session.id, into=successor)
        assert str(wrapped.document.path).startswith("10_Workspaces/Demo-Two/Logs/2026/")
        assert wrapped.document.frontmatter["workspace"] == str(successor)
        assert wrapped.redirected_from == session.workspace

    def test_the_log_says_it_was_directed(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        root: Path,
        documents: FileSystemMarkdownStore,
    ) -> None:
        # A redirected log says so, and that holds for an explicit target too:
        # a reader of Demo Two's logs finds a session that opened somewhere
        # else, and should not have to guess.
        _checkpoint(sessions, session, "Merged Demo into Demo Two")
        successor = content.create_workspace("Demo Two", workspace_type="product").concept_id
        self._gone(root, documents)
        wrapped = wrap.wrap(session.id, into=successor)
        assert str(session.workspace) in wrapped.document.body
        assert "no longer in the vault" in wrapped.document.body

    def test_a_log_directed_where_the_session_opened_says_nothing_about_it(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        workspace: ConceptId,
    ) -> None:
        _checkpoint(sessions, session, "Nothing moved")
        wrapped = wrap.wrap(session.id, into=workspace)
        assert wrapped.redirected_from is None
        assert "no longer in the vault" not in wrapped.document.body

    def test_the_log_can_be_directed_into_a_life_area(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        root: Path,
        documents: FileSystemMarkdownStore,
    ) -> None:
        # A workspace that became a life area: 10_Workspaces/Education became
        # the 20_Life/Education area, and the log belongs to the area (core/02
        # section 16.3), foldered by year as anywhere else.
        _checkpoint(sessions, session, "Education becomes a life area")
        area = content.create_life_area("Education").concept_id
        self._gone(root, documents)
        wrapped = wrap.wrap(session.id, into=area)
        assert str(wrapped.document.path).startswith("20_Life/Education/Logs/2026/2026-08-26_")
        assert wrapped.document.frontmatter["area"] == str(area)
        assert "workspace" not in wrapped.document.frontmatter
        assert (root / str(wrapped.document.path)).is_file()

    def test_an_archived_workspace_still_takes_its_own_log(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        root: Path,
        documents: FileSystemMarkdownStore,
    ) -> None:
        # Archiving moves a workspace; its identity travels with it, so the
        # session's own id still resolves and the log lands beside the rest
        # of that workspace's history. Nothing to pass.
        _checkpoint(sessions, session, "Retired Demo")
        archive = root / "90_Archive" / "Workspaces"
        archive.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root / "10_Workspaces" / "Demo"), str(archive / "Demo"))
        documents.refresh()
        wrapped = wrap.wrap(session.id)
        assert str(wrapped.document.path).startswith("90_Archive/Workspaces/Demo/Logs/2026/")
        assert wrapped.redirected_from is None

    def test_a_target_that_is_not_there_is_refused(
        self, wrap: WrapService, sessions: InMemorySessionStore, session: Session
    ) -> None:
        _checkpoint(sessions, session, "Anything")
        with pytest.raises(Never4gaError, match="is not in the vault"):
            wrap.wrap(session.id, into=ConceptId.new())

    def test_a_target_that_is_neither_workspace_nor_area_is_refused(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        workspace: ConceptId,
    ) -> None:
        _checkpoint(sessions, session, "Anything")
        note = content.create_concept("decision", "Not a home", workspace=workspace).concept_id
        with pytest.raises(Never4gaError, match="workspace or a life area"):
            wrap.wrap(session.id, into=note)

    def test_a_second_wrap_stays_where_the_first_landed(
        self,
        wrap: WrapService,
        sessions: InMemorySessionStore,
        session: Session,
        content: ContentService,
        root: Path,
        documents: FileSystemMarkdownStore,
    ) -> None:
        # Idempotence is about one session leaving one record. A target on a
        # rewrite is not a move; the record stays where it was first written.
        _checkpoint(sessions, session, "Merged Demo into Demo Two")
        successor = content.create_workspace("Demo Two", workspace_type="product").concept_id
        self._gone(root, documents)
        first = wrap.wrap(session.id, into=successor)
        _checkpoint(sessions, session, "And then some", minutes=30)
        again = wrap.wrap(session.id)
        assert again.document.path == first.document.path
        assert again.updated
