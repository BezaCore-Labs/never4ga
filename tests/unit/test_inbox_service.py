"""The Inbox is cleared by accounting for things, never by deleting them.

An item is filed as a ticket, a knowledge note or whatever it turns out to be.
Nothing leaves the Inbox until it is accounted for, and once it is accounted
for it leaves. An item filed and left behind makes the pile permanent; an item
removed without being filed is a thought thrown away.

"Accounted for" is checked, not asserted. A concept must exist in the vault. A
work item must have a write this session actually recorded against it: an
agent saying it filed a ticket is a claim, and the session store's
`work_actions` is a fact. `wrap` draws the same line.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from never4ga.adapters.fakes import InMemoryDocumentStore, InMemoryVaultFileStore
from never4ga.adapters.fakes.session_store import InMemorySessionStore
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId, SessionId
from never4ga.domain.sessions import Session, WorkAction
from never4ga.services.inbox import InboxError, InboxService


@pytest.fixture
def files() -> InMemoryVaultFileStore:
    return InMemoryVaultFileStore()


@pytest.fixture
def documents() -> InMemoryDocumentStore:
    return InMemoryDocumentStore()


@pytest.fixture
def sessions() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def inbox(
    files: InMemoryVaultFileStore,
    documents: InMemoryDocumentStore,
    sessions: InMemorySessionStore,
) -> InboxService:
    return InboxService(files, documents, sessions)


def capture(files: InMemoryVaultFileStore, name: str = "2026-09-01_a-thought.md") -> VaultPath:
    path = VaultPath.parse(f"00_Inbox/{name}")
    files.write_text(path, "# a thought\n\nsomething worth keeping\n")
    return path


def _a_session(sessions: InMemorySessionStore) -> SessionId:
    session = Session(
        id=SessionId.new(),
        workspace=ConceptId.new(),
        actor="claude-code/claude-opus-5",
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
        client="claude-code",
    )
    sessions.open(session)
    return session.id


def a_concept(documents: InMemoryDocumentStore) -> ConceptId:
    concept_id = ConceptId.new()
    documents.put(
        StoredDocument(
            concept_id=concept_id,
            path=VaultPath.parse("30_Knowledge/Notes/filed.md"),
            frontmatter={
                "type": "knowledge",
                "id": str(concept_id),
                "schema": "never4ga/0.1",
                "title": "Filed",
                "created_at": "2026-09-01T12:00:00Z",
            },
            body="# Filed\n",
        )
    )
    return concept_id


class TestWhatIsPending:
    def test_it_lists_captures_oldest_first(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        capture(files, "2026-09-02_later.md")
        capture(files, "2026-08-30_earlier.md")
        assert [str(path) for path in inbox.pending()] == [
            "00_Inbox/2026-08-30_earlier.md",
            "00_Inbox/2026-09-02_later.md",
        ]

    def test_reserved_navigation_is_not_pending(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        files.write_text(VaultPath.parse("00_Inbox/index.md"), "# Inbox\n")
        files.write_text(VaultPath.parse("00_Inbox/log.md"), "history\n")
        assert inbox.pending() == ()

    def test_nothing_outside_the_inbox_is_pending(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        files.write_text(VaultPath.parse("30_Knowledge/Notes/thing.md"), "# note\n")
        assert inbox.pending() == ()


class TestFilingIntoAConcept:
    def test_a_concept_that_exists_accounts_for_the_item(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        path = capture(files)
        concept = a_concept(documents)
        resolved = inbox.resolve(path, concept=concept)
        assert resolved.became == str(concept)
        assert not files.exists(path)

    def test_a_concept_that_does_not_exist_leaves_the_item_alone(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        path = capture(files)
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path, concept=ConceptId.new())
        assert "is not in this vault" in str(raised.value)
        assert files.exists(path)


class TestFilingIntoAWorkItem:
    def test_a_recorded_write_accounts_for_the_item(
        self, inbox: InboxService, files: InMemoryVaultFileStore, sessions: InMemorySessionStore
    ) -> None:
        path = capture(files)
        session = _a_session(sessions)
        sessions.record_work_action(
            WorkAction(
                session=session,
                recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
                item="970",
                verb="create",
            )
        )
        resolved = inbox.resolve(path, work_item="970", session=session)
        assert resolved.became == "970"
        assert not files.exists(path)

    def test_a_claim_with_no_recorded_write_is_refused(
        self, inbox: InboxService, files: InMemoryVaultFileStore, sessions: InMemorySessionStore
    ) -> None:
        # An agent saying it filed a ticket is a declaration; the session
        # store is the observation. `wrap` already draws this line.
        path = capture(files)
        session = _a_session(sessions)
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path, work_item="970", session=session)
        assert "no write" in str(raised.value)
        assert files.exists(path)


class TestTheGuardrails:
    def test_an_item_outside_the_inbox_is_refused(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        # The verb removes files. It may only ever remove one from the Inbox.
        outside = VaultPath.parse("30_Knowledge/Notes/precious.md")
        files.write_text(outside, "# precious\n")
        with pytest.raises(InboxError) as raised:
            inbox.resolve(outside, concept=a_concept(documents))
        assert "only clears" in str(raised.value)
        assert files.exists(outside)

    def test_an_item_that_is_not_there_is_refused(
        self, inbox: InboxService, documents: InMemoryDocumentStore
    ) -> None:
        with pytest.raises(InboxError):
            inbox.resolve(
                VaultPath.parse("00_Inbox/2026-09-01_gone.md"), concept=a_concept(documents)
            )

    def test_saying_nothing_about_where_it_went_is_refused(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        path = capture(files)
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path)
        assert "accounted for" in str(raised.value)
        assert files.exists(path)


class TestDiscarding:
    """Not everything captured deserves a home, and pretending otherwise
    leaves junk in the Inbox forever. A discard is still an accounting: it
    names a reason, and the vault's Git history keeps the body."""

    def test_a_discard_needs_a_reason(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        path = capture(files)
        with pytest.raises(InboxError):
            inbox.resolve(path, discard=True)
        assert files.exists(path)

    def test_a_reasoned_discard_clears_the_item(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        path = capture(files)
        resolved = inbox.resolve(path, discard=True, reason="duplicate of the note above")
        assert resolved.became is None
        assert resolved.reason == "duplicate of the note above"
        assert not files.exists(path)


class TestADestinationThatWillNeverBeSeenAgain:
    """Existing is not the same as resurfacing.

    The Inbox exists so things are not forgotten, so an item may only leave for
    a place that will bring it back. The goal lane queries by
    ``workspace_ids``, so an area-scoped goal reaches no pack and would be
    found only by someone who already remembers it.

    The check is narrow, because classification stays off the mechanical path
    (core/07 section 1) and this verb must not guess what an item should have
    become. It asks one question: the pack carries this type, so could it carry
    this document? A `knowledge` note never claims to arrive unasked; it is
    reached by searching, so filing a thought there is a complete disposal. A
    `goal` does claim it, and one the lane cannot see is refused.
    """

    def _goal(
        self,
        documents: InMemoryDocumentStore,
        path: str,
        *,
        workspace: ConceptId | None = None,
        area: ConceptId | None = None,
    ) -> ConceptId:
        concept_id = ConceptId.new()
        frontmatter: dict[str, object] = {
            "type": "goal",
            "id": str(concept_id),
            "schema": "never4ga/0.1",
            "title": "Something to achieve",
            "created_at": "2026-09-02T12:00:00Z",
            "lifecycle": "proposed",
        }
        if workspace is not None:
            frontmatter["workspace"] = str(workspace)
        if area is not None:
            frontmatter["area"] = str(area)
        documents.put(
            StoredDocument(
                concept_id=concept_id,
                path=VaultPath.parse(path),
                frontmatter=frontmatter,
                body="# Something to achieve\n",
            )
        )
        return concept_id

    def test_an_area_scoped_goal_reaches_no_lane_so_the_item_stays(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        path = capture(files)
        goal = self._goal(documents, "20_Life/Education/reading-plan.md", area=ConceptId.new())
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path, concept=goal)
        assert "will not resurface" in str(raised.value)
        assert files.exists(path)

    def test_the_refusal_names_the_type_and_the_path(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        path = capture(files)
        goal = self._goal(documents, "20_Life/Education/reading-plan.md", area=ConceptId.new())
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path, concept=goal)
        message = str(raised.value)
        assert "goal" in message
        assert "20_Life/Education/reading-plan.md" in message

    def test_a_workspace_scoped_goal_does_reach_a_lane(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        path = capture(files)
        goal = self._goal(
            documents,
            "10_Workspaces/Never4gA/Goals/ship-it.md",
            workspace=ConceptId.new(),
        )
        resolved = inbox.resolve(path, concept=goal)
        assert resolved.became == str(goal)
        assert not files.exists(path)

    def test_reference_material_is_still_a_complete_disposal(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        # A knowledge note carries no workspace and enters no structural lane,
        # and that is what it is for. Refusing it would leave every durable
        # thought stuck in the Inbox, which is the pile this verb exists to
        # empty.
        path = capture(files)
        resolved = inbox.resolve(path, concept=a_concept(documents))
        assert resolved.became is not None
        assert not files.exists(path)

    def test_an_archived_destination_is_refused(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        # `is_detached` excludes 90_Archive from every structural lane, so a
        # workspace id does not save it.
        path = capture(files)
        goal = self._goal(
            documents,
            "90_Archive/Lantern/Goals/ship-it.md",
            workspace=ConceptId.new(),
        )
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path, concept=goal)
        assert "will not resurface" in str(raised.value)
        assert files.exists(path)

    def test_a_standard_reaches_its_lane_without_a_workspace(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        # The standard lane queries every standard in the vault, with no
        # workspace filter -- so a vault-wide standard genuinely does resurface.
        path = capture(files)
        concept_id = ConceptId.new()
        documents.put(
            StoredDocument(
                concept_id=concept_id,
                path=VaultPath.parse("50_System/Standards/how-we-work.md"),
                frontmatter={
                    "type": "standard",
                    "id": str(concept_id),
                    "schema": "never4ga/0.1",
                    "title": "How we work",
                    "created_at": "2026-09-02T12:00:00Z",
                },
                body="# How we work\n",
            )
        )
        resolved = inbox.resolve(path, concept=concept_id)
        assert resolved.became == str(concept_id)
        assert not files.exists(path)


class TestTheRefusalExplainsItself:
    """A hint that contradicts the failure is worse than no hint.

    "File it first, then clear it" is right when the destination does not
    exist. It is wrong when the destination exists and cannot be seen again,
    because the item is filed and the advice sends someone to repeat what they
    just did.
    """

    def test_an_unreachable_destination_carries_its_own_hint(
        self, inbox: InboxService, files: InMemoryVaultFileStore, documents: InMemoryDocumentStore
    ) -> None:
        path = capture(files)
        concept_id = ConceptId.new()
        documents.put(
            StoredDocument(
                concept_id=concept_id,
                path=VaultPath.parse("20_Life/Education/a-goal.md"),
                frontmatter={
                    "type": "goal",
                    "id": str(concept_id),
                    "schema": "never4ga/0.1",
                    "title": "A goal",
                    "created_at": "2026-09-02T12:00:00Z",
                    "lifecycle": "proposed",
                    "area": str(ConceptId.new()),
                },
                body="# A goal\n",
            )
        )
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path, concept=concept_id)
        assert raised.value.hint is not None
        assert "not a filing error" in raised.value.hint

    def test_a_missing_destination_keeps_the_generic_hint(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        path = capture(files)
        with pytest.raises(InboxError) as raised:
            inbox.resolve(path, concept=ConceptId.new())
        assert raised.value.hint is None


class TestTheScratchpad:
    """A place for a thought too small to be a note.

    `capture` writes a file per thought, which suits a paragraph and is heavy
    for a line. The scratchpad is one durable file that stays in the Inbox and
    gets drained. `pending()` never lists it, because it is only ever emptied.
    Its lines leave under the same accounting as a captured file, since a line
    removed without one is a thought thrown away just as surely.
    """

    def test_the_scratchpad_is_furniture_not_a_pending_item(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        inbox.scratch("a thought")
        assert inbox.pending() == ()

    def test_a_thought_can_be_written_before_the_file_exists(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        line = inbox.scratch("compare two note apps, check the second too")
        assert line.text == "compare two note apps, check the second too"
        assert line.number == 1
        assert files.exists(VaultPath.parse("00_Inbox/scratchpad.md"))

    def test_thoughts_keep_the_order_they_arrived_in(self, inbox: InboxService) -> None:
        inbox.scratch("first")
        inbox.scratch("second")
        inbox.scratch("third")
        assert [line.text for line in inbox.scratch_lines()] == ["first", "second", "third"]
        assert [line.number for line in inbox.scratch_lines()] == [1, 2, 3]

    def test_an_empty_thought_is_refused(self, inbox: InboxService) -> None:
        with pytest.raises(InboxError):
            inbox.scratch("   ")

    def test_a_thought_written_by_hand_in_obsidian_counts(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        # No managed markers, deliberately. People type into this file in
        # Obsidian, and a bullet that lands outside a fenced region and is
        # silently ignored is exactly the trap the scratchpad exists to avoid.
        files.write_text(
            VaultPath.parse("00_Inbox/scratchpad.md"),
            "# Scratchpad\n\nQuick thoughts.\n\n- typed straight into obsidian\n",
        )
        assert [line.text for line in inbox.scratch_lines()] == ["typed straight into obsidian"]

    def test_prose_around_the_thoughts_is_left_alone(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        path = VaultPath.parse("00_Inbox/scratchpad.md")
        files.write_text(path, "# Scratchpad\n\nQuick thoughts, one per line.\n\n- one\n")
        inbox.scratch("two")
        text = files.read_text(path) or ""
        assert "Quick thoughts, one per line." in text
        assert text.count("- one") == 1
        assert "- two" in text


class TestDrainingTheScratchpad:
    def test_a_line_leaves_for_a_concept_that_resurfaces(
        self, inbox: InboxService, documents: InMemoryDocumentStore
    ) -> None:
        inbox.scratch("keep me")
        inbox.scratch("file me")
        resolved = inbox.resolve_scratch(2, concept=a_concept(documents))
        assert resolved.became is not None
        assert [line.text for line in inbox.scratch_lines()] == ["keep me"]

    def test_a_line_leaves_for_a_work_item_this_session_wrote(
        self, inbox: InboxService, sessions: InMemorySessionStore
    ) -> None:
        inbox.scratch("water the plants")
        session = _a_session(sessions)
        sessions.record_work_action(
            WorkAction(
                session=session,
                recorded_at=datetime(2026, 9, 2, tzinfo=UTC),
                item="978",
                verb="create",
            )
        )
        resolved = inbox.resolve_scratch(1, work_item="978", session=session)
        assert resolved.became == "978"
        assert inbox.scratch_lines() == ()

    def test_a_line_can_be_discarded_with_a_reason(self, inbox: InboxService) -> None:
        inbox.scratch("never mind")
        resolved = inbox.resolve_scratch(1, discard=True, reason="thought better of it")
        assert resolved.became is None
        assert inbox.scratch_lines() == ()

    def test_draining_the_last_line_keeps_the_file_and_its_header(
        self, inbox: InboxService, files: InMemoryVaultFileStore
    ) -> None:
        # The scratchpad is drained, never deleted, or the next thought would
        # have nowhere visible to go.
        inbox.scratch("the only thought")
        inbox.resolve_scratch(1, discard=True, reason="done with it")
        text = files.read_text(VaultPath.parse("00_Inbox/scratchpad.md"))
        assert text is not None
        assert text.startswith("# Scratchpad")
        assert inbox.scratch_lines() == ()

    def test_a_line_is_not_removed_without_an_accounting(self, inbox: InboxService) -> None:
        inbox.scratch("keep me")
        with pytest.raises(InboxError):
            inbox.resolve_scratch(1)
        assert [line.text for line in inbox.scratch_lines()] == ["keep me"]

    def test_a_line_bound_for_somewhere_it_would_be_forgotten_stays(
        self, inbox: InboxService, documents: InMemoryDocumentStore
    ) -> None:
        # The same rule a captured file leaves under. A line is smaller; it is
        # not more expendable.
        inbox.scratch("the reading plan")
        concept_id = ConceptId.new()
        documents.put(
            StoredDocument(
                concept_id=concept_id,
                path=VaultPath.parse("20_Life/Education/a-goal.md"),
                frontmatter={
                    "type": "goal",
                    "id": str(concept_id),
                    "schema": "never4ga/0.1",
                    "title": "A goal",
                    "created_at": "2026-09-02T12:00:00Z",
                    "lifecycle": "proposed",
                },
                body="# A goal\n",
            )
        )
        with pytest.raises(InboxError) as raised:
            inbox.resolve_scratch(1, concept=concept_id)
        assert "will not resurface" in str(raised.value)
        assert len(inbox.scratch_lines()) == 1

    def test_a_line_that_is_not_there_is_refused(
        self, inbox: InboxService, documents: InMemoryDocumentStore
    ) -> None:
        inbox.scratch("only one")
        with pytest.raises(InboxError) as raised:
            inbox.resolve_scratch(7, concept=a_concept(documents))
        assert "7" in str(raised.value)
        assert len(inbox.scratch_lines()) == 1
