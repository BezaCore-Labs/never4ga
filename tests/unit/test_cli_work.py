"""The write verbs' two gates, and what each refusal says.

core/03 section 22: read, draft/proposed write and actual write are three
distinct things. The first gate is `sync_policy` on the workspace (core/03
`sync_policy`); the second is `--apply` on the invocation. Without `--apply` a
verb proposes and sends nothing.
"""

from __future__ import annotations

import argparse
import io
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath
from typing import Any, Final

import pytest

from never4ga import cli
from never4ga.adapters.fakes import (
    FakeRepositoryLocator,
    FakeWorkManagementWriter,
    InMemoryDocumentStore,
    InMemorySecretStore,
    InMemoryWorkspaceMappingStore,
)
from never4ga.cli import _Context, _record_work_action
from never4ga.cli.output import Format, Reporter
from never4ga.composition import FileSessionStore
from never4ga.domain.connections import Connection
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId, ExternalId
from never4ga.domain.work_policy import WriteDisposition, WritePolicy
from never4ga.errors import SessionStoreError, StructuredError
from never4ga.ports.work_management import WorkItem
from never4ga.services.connections import secret_ref_for
from never4ga.services.sessions import SessionService
from never4ga.services.work_writing import Bound, WorkWriteService
from never4ga.services.workspaces import WorkspaceService
from tests.openproject_fixtures import DownTracker, TrackerWriter

WORKSPACE = ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbb6")
SESSION = "01a0c1be-f64a-76b0-8c96-bff5b4a95382"
WORKSPACE_PATH = VaultPath.parse("10_Workspaces/Example/workspace.md")
REPOSITORY = PurePath("/home/someone/Projects/example")
CONNECTION = Connection(
    name="work_openproject", provider="fake-pm", base_url="https://pm.example.dev"
)
ITEM = WorkItem(
    ref=ExternalId(provider="fake-pm", value="838"),
    title="Milestone 9",
    status="New",
)
FAKE_WRITER = FakeWorkManagementWriter([ITEM])


def workspace_document(**work_management: Any) -> StoredDocument:
    frontmatter: dict[str, Any] = {
        "type": "workspace",
        "id": str(WORKSPACE),
        "schema": "never4ga/0.1",
        "title": "Example",
        "created_at": "2026-08-26T00:00:00Z",
    }
    if work_management:
        frontmatter["work_management"] = work_management
    return StoredDocument(
        concept_id=WORKSPACE, path=WORKSPACE_PATH, frontmatter=frontmatter, body="# Example\n"
    )


def writing(
    documents: InMemoryDocumentStore | None = None,
    secrets: InMemorySecretStore | None = None,
    hidden: Sequence[PurePath] = (),
) -> WorkWriteService:
    """The service with a factory that hands back whatever the test built."""
    store = secrets or InMemorySecretStore()

    def factory(connection: Connection) -> Any:
        # Stands in for the composition root's `writer_for`: a connection with
        # no token has no writable adapter, which is the case `bind` reports.
        return None if store.get(secret_ref_for(connection)) is None else FAKE_WRITER

    return WorkWriteService(
        documents=documents or InMemoryDocumentStore(),
        workspaces=workspaces(hidden),
        factory=factory,
    )


def workspaces(hidden: Sequence[PurePath] = ()) -> WorkspaceService:
    store = InMemoryWorkspaceMappingStore()
    service = WorkspaceService(store, FakeRepositoryLocator([REPOSITORY], hidden=hidden))
    service.map(workspace_id=WORKSPACE, workspace_path=WORKSPACE_PATH, repository_root=REPOSITORY)
    return service


def bound(*, sync_policy: str = "read_write", applying: bool = False) -> Bound:
    writer = FakeWorkManagementWriter([ITEM])
    return Bound(
        workspace=workspace_document(
            mode="external",
            connection=CONNECTION.name,
            provider="fake-pm",
            project_ref="example",
            sync_policy=sync_policy,
        ),
        connection=CONNECTION,
        writer=writer,
        policy=WritePolicy(mode="external", sync_policy=sync_policy, applying=applying),
    )


class TestBinding:
    def test_a_workspace_with_no_tracker_says_so_and_says_what_to_do(self) -> None:
        documents = InMemoryDocumentStore()
        documents.put(workspace_document())
        result = writing(documents).bind(REPOSITORY, applying=False)
        assert isinstance(result, StructuredError)
        assert result.code == "work_management_undeclared"
        assert "link" in (result.repair_hint or "")

    def test_an_unresolvable_directory_refuses_rather_than_guessing(self) -> None:
        documents = InMemoryDocumentStore()
        documents.put(workspace_document())
        result = writing(documents).bind(PurePath("/somewhere/else"), applying=False)
        assert isinstance(result, StructuredError)
        assert result.code == "scope_unresolved"

    def test_a_directory_that_cannot_be_seen_says_so_rather_than_unmapped(self) -> None:
        # The work verbs bind through the same resolution a context pack uses,
        # so a directory the service cannot see is reported the same way.
        documents = InMemoryDocumentStore()
        documents.put(workspace_document())
        hidden = PurePath("/tmp/scratch/worktree")
        result = writing(documents, hidden=[hidden]).bind(hidden, applying=False)
        assert isinstance(result, StructuredError)
        assert result.code == "path_not_visible"
        assert "--local" in (result.repair_hint or "")

    def test_a_declared_tracker_with_no_token_is_not_writable(self) -> None:
        documents = InMemoryDocumentStore()
        documents.put(
            workspace_document(
                mode="external",
                connection="work_openproject",
                provider="openproject",
                project_ref="example",
                sync_policy="read_write",
            )
        )
        documents.put(
            StoredDocument(
                concept_id=ConceptId.parse("01a03428-7d75-703a-8b55-58b8d820bbc0"),
                path=VaultPath.parse("50_System/Integrations/work-openproject.md"),
                frontmatter={
                    "type": "integration",
                    "id": "01a03428-7d75-703a-8b55-58b8d820bbc0",
                    "schema": "never4ga/0.1",
                    "title": "Work OpenProject",
                    "created_at": "2026-08-26T00:00:00Z",
                    "connection": "work_openproject",
                    "provider": "openproject",
                    "base_url": "https://pm.example.dev",
                },
                body="",
            )
        )
        result = writing(documents).bind(REPOSITORY, applying=False)
        assert isinstance(result, StructuredError)
        assert result.code == "connection_not_writable"
        assert "set-token" in (result.repair_hint or "")


class TestTheFirstGate:
    @pytest.mark.parametrize("sync_policy", ["read", "reference"])
    def test_a_workspace_that_does_not_permit_writing_refuses(self, sync_policy: str) -> None:
        result = writing().update(
            bound(sync_policy=sync_policy, applying=True), "838", {"status": "Closed"}
        )
        assert isinstance(result, StructuredError)
        assert result.code == "write_not_permitted"
        assert "read_write" in result.message

    def test_the_refusal_says_the_change_is_a_persons_to_make(self) -> None:
        result = writing().update(bound(sync_policy="read", applying=True), "838", {"status": "x"})
        assert isinstance(result, StructuredError)
        assert "by hand" in (result.repair_hint or "")

    def test_creating_and_commenting_are_gated_the_same_way(self) -> None:
        for result in (
            writing().create(bound(sync_policy="read", applying=True), {"title": "x"}),
            writing().comment(bound(sync_policy="read", applying=True), "838", "why"),
        ):
            assert isinstance(result, StructuredError)
            assert result.code == "write_not_permitted"


class TestTheSecondGate:
    def test_without_apply_the_change_is_described_and_not_sent(self) -> None:
        context = bound(applying=False)
        result = writing().update(context, "838", {"status": "Closed"})
        assert not isinstance(result, StructuredError)
        assert result["applied"] is False
        assert "New -> Closed" in result["proposal"]
        # The item really is untouched, which is the whole claim.
        still = context.writer.get_work_item(ITEM.ref)
        assert still is not None
        assert still.status == "New"

    def test_with_apply_the_change_is_sent(self) -> None:
        context = bound(applying=True)
        result = writing().update(context, "838", {"status": "Closed"})
        assert not isinstance(result, StructuredError)
        assert result["applied"] is True
        assert result["item"]["status"] == "Closed"

    def test_a_comment_is_gated_the_same_way_as_a_field_change(self) -> None:
        # Uniform on purpose: an exception is one more thing to remember, and a
        # comment on the wrong ticket is still noise.
        context = bound(applying=False)
        result = writing().comment(context, "838", "why this closed")
        assert not isinstance(result, StructuredError)
        assert result["applied"] is False

    def test_applying_a_comment_returns_its_identity(self) -> None:
        result = writing().comment(bound(applying=True), "838", "why this closed")
        assert not isinstance(result, StructuredError)
        assert result["activity"]

    def test_a_comment_may_replace_the_one_it_made_before(self) -> None:
        context = bound(applying=True)
        first = writing().comment(context, "838", "first account")
        assert not isinstance(first, StructuredError)
        second = writing().comment(context, "838", "corrected", amends=first["activity"])
        assert not isinstance(second, StructuredError)
        assert second["activity"] == first["activity"]


class TestNoOp:
    def test_a_change_to_nothing_stops_before_sending_even_with_apply(self) -> None:
        # Applying it would still bump a journal entry on the tracker, which
        # says somebody changed something when nobody did.
        context = bound(applying=True)
        result = writing().update(context, "838", {"status": "New"})
        assert not isinstance(result, StructuredError)
        assert result["noop"] is True
        assert result["applied"] is False
        assert "nothing would change" in result["proposal"]


class TestFailures:
    """An item the tracker does not have is ``work_item_not_found``.

    The read side uses the same code, and an agent branches on it to say
    "check the reference" rather than "the tracker refused the values".
    """

    @pytest.mark.parametrize(
        "verb",
        [
            lambda service, context: service.update(context, "does-not-exist", {"status": "x"}),
            lambda service, context: service.comment(context, "does-not-exist", "why"),
            lambda service, context: service.relate(
                context, "does-not-exist", "838", kind="relates"
            ),
            lambda service, context: service.relate(
                context, "838", "does-not-exist", kind="relates"
            ),
        ],
        ids=["update", "comment", "relate-from", "relate-to"],
    )
    def test_an_item_the_tracker_does_not_have_is_reported_as_missing(
        self, verb: Callable[[WorkWriteService, Bound], Any]
    ) -> None:
        result = verb(writing(), bound(applying=True))
        assert isinstance(result, StructuredError)
        assert result.code == "work_item_not_found"
        assert "does-not-exist" in result.message
        assert result.retryable is False

    def test_a_write_that_fails_is_reported_rather_than_raised(self) -> None:
        context = bound(applying=True)
        proposal_first = writing().update(context, "838", {"status": "Closed"})
        assert not isinstance(proposal_first, StructuredError)
        # Applying the same stale proposal again is a conflict, and the verb
        # turns it into something a caller can print rather than a traceback.
        stale = context.writer.propose_update(ITEM.ref, {"status": "Closed"})
        context.writer.apply(context.writer.propose_update(ITEM.ref, {"status": "Rejected"}))
        with pytest.raises(Exception, match="moved on"):
            context.writer.apply(stale)


DOWN: Final = DownTracker()

#: Every write verb, as the service is asked for it.
VERBS: Final = {
    "update": lambda service, context: service.update(context, "838", {"status": "Closed"}),
    "create": lambda service, context: service.create(context, {"title": "A new item"}),
    "comment": lambda service, context: service.comment(context, "838", "why this closed"),
    "relate": lambda service, context: service.relate(context, "838", "839", kind="relates"),
}


class TestAnUnreachableTracker:
    """A tracker that did not answer is not reported as one that cannot.

    Every write verb says `tracker_unreachable` and retryable, in the same
    words as the read side. Reporting it as unsupported or `propose_failed`
    would tell an agent to give up on a write it should retry.
    """

    @pytest.mark.parametrize("verb", sorted(VERBS))
    def test_every_verb_says_the_tracker_did_not_answer(self, verb: str) -> None:
        result = VERBS[verb](DOWN, DOWN.bind())
        assert isinstance(result, StructuredError)
        assert result.code == "tracker_unreachable"
        assert "did not answer" in result.message
        assert "does not support" not in result.message

    @pytest.mark.parametrize("verb", sorted(VERBS))
    def test_and_that_it_is_worth_trying_again(self, verb: str) -> None:
        result = VERBS[verb](DOWN, DOWN.bind())
        assert isinstance(result, StructuredError)
        assert result.retryable is True
        assert result.repair_hint and "connection health work_openproject" in result.repair_hint

    def test_a_proposal_alone_says_so_too(self) -> None:
        # Without --apply nothing would be sent, but the draft still needs the
        # instance: a draft of something that cannot be checked is not one.
        result = DOWN.comment(DOWN.bind(applying=False), "838", "why this closed")
        assert isinstance(result, StructuredError)
        assert result.code == "tracker_unreachable"

    def test_a_tracker_that_answered_no_is_still_not_retryable(self) -> None:
        context = bound(applying=True)
        refusing = FakeWorkManagementWriter([ITEM], writable=False)
        result = writing().comment(
            Bound(
                workspace=context.workspace,
                connection=context.connection,
                writer=refusing,
                policy=context.policy,
            ),
            "838",
            "why this closed",
        )
        assert isinstance(result, StructuredError)
        assert result.code == "propose_failed"
        assert result.retryable is False
        assert "does not support" in result.message


class TestAMissingItemThroughTheRealAdapter:
    """A missing item, from OpenProject's own answer: a 404 for the work package."""

    @pytest.mark.parametrize("verb", sorted(VERBS.keys() - {"create"}))
    def test_every_verb_that_names_an_item_says_it_is_missing(self, verb: str) -> None:
        tracker = TrackerWriter()
        naming = {
            "update": lambda: tracker.update(tracker.bind(), "999", {"status": "Closed"}),
            "comment": lambda: tracker.comment(tracker.bind(), "999", "why this closed"),
            "relate": lambda: tracker.relate(tracker.bind(), "999", "839", kind="relates"),
        }
        result = naming[verb]()
        assert isinstance(result, StructuredError)
        assert result.code == "work_item_not_found"
        assert result.retryable is False


class TestTheCliSaysATrackerDidNotAnswer:
    """Every write verb on the CLI reports a down tracker as unreachable.

    Driven through `main` with only the vault binding replaced. The service,
    the adapter and the reporter are the real ones.
    """

    @pytest.mark.parametrize(
        "verb",
        [
            ["update", "838", "--field", "status=Closed"],
            ["create", "A new item"],
            ["comment", "838", "why this closed"],
            ["relate", "838", "839"],
        ],
        ids=["update", "create", "comment", "relate"],
    )
    def test_every_verb_says_so_and_fails(
        self,
        verb: list[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import json

        monkeypatch.setattr(cli, "_writing", lambda _: DOWN)
        monkeypatch.setattr(cli, "_bound", lambda _, arguments: DOWN.bind(applying=arguments.apply))

        code = cli.main(["--vault", str(tmp_path), "--json", "work", *verb, "--apply"])

        assert code == cli.EXIT_FAILED
        error = json.loads(capsys.readouterr().err)["error"]
        assert error["code"] == "tracker_unreachable"
        assert error["retryable"] is True
        assert "does not support" not in error["message"]


class TestDisposition:
    def test_the_policy_decides_all_three_outcomes(self) -> None:
        assert bound(sync_policy="read").policy.disposition is WriteDisposition.REFUSE
        assert bound(applying=False).policy.disposition is WriteDisposition.PROPOSE
        assert bound(applying=True).policy.disposition is WriteDisposition.APPLY


class _UnusableSessionStore:
    """A session store that is there and cannot be used.

    What the port raises when the database is locked past its busy timeout,
    the disk is full, a migration fails or the file is corrupt.
    """

    def record_work_action(self, action: Any) -> None:
        raise SessionStoreError("database is locked")

    def __getattr__(self, name: str) -> Any:
        raise SessionStoreError("database is locked")


class TestTheCliSaysWhenARecordWasLost:
    """`work ... --session` reports a lost session record instead of raising.

    By the time the session store fails, the tracker has already changed. A
    traceback and a failing exit code would misreport what happened, so the
    command says the write landed and the record was lost.
    """

    @pytest.fixture
    def said(self) -> io.StringIO:
        """What the command wrote to stderr, held explicitly.

        Given to the reporter rather than read back through ``capsys``: the
        reporter binds its stream once, at construction, and a fixture built
        before capture starts would write past it.
        """
        return io.StringIO()

    @pytest.fixture
    def reporter(self, said: io.StringIO) -> Reporter:
        return Reporter(Format.HUMAN, stderr=said)

    @pytest.fixture
    def context(self, tmp_path: Path, reporter: Reporter) -> _Context:
        return _Context(root=tmp_path, reporter=reporter)

    @pytest.fixture
    def arguments(self) -> argparse.Namespace:
        return argparse.Namespace(work_item="840", session=SESSION, apply=True)

    def test_the_payload_says_the_record_was_lost(
        self, context: _Context, arguments: argparse.Namespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_session_store", lambda _: _UnusableSessionStore())

        payload = _record_work_action(context, arguments, {"applied": True}, "comment")

        assert "database is locked" in payload["record_lost"]

    def test_it_is_also_said_in_english_on_stderr(
        self,
        context: _Context,
        arguments: argparse.Namespace,
        monkeypatch: pytest.MonkeyPatch,
        said: io.StringIO,
    ) -> None:
        # The human mode prints a summary rather than the payload, so somebody
        # reading it would otherwise see an unqualified success.
        monkeypatch.setattr(cli, "_session_store", lambda _: _UnusableSessionStore())

        _record_work_action(context, arguments, {"applied": True}, "comment")

        assert "was not recorded against this session" in said.getvalue()
        assert "do not retry the write" in said.getvalue()

    def test_an_ordinary_write_says_nothing_and_changes_nothing(
        self,
        context: _Context,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        said: io.StringIO,
    ) -> None:
        store = FileSessionStore(tmp_path / "sessions.sqlite3")
        session = SessionService(store).open(workspace=WORKSPACE, actor="a-client").id
        monkeypatch.setattr(cli, "_session_store", lambda _: store)
        arguments = argparse.Namespace(work_item="840", session=str(session), apply=True)

        payload = _record_work_action(context, arguments, {"applied": True}, "comment")

        assert payload == {"applied": True}
        assert said.getvalue() == ""
