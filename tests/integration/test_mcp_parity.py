"""MCP and the CLI answering the same question, against a real vault.

**Identical payloads.** The same request produces byte-identical structured
output through either surface, because both render one service result through
`never4ga.rendering`. A test that only compared *meaning* could not fail when
two renderers drift, and drifting renderers are how `core/05` section 13's
"three interfaces over the same services" quietly stops being true.

The comparison is of the *structured* half. MCP returns content blocks and the
CLI prints a human summary beside its JSON; what has to be identical is the
payload, and these assert exactly that.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import main
from never4ga.mcp.toolbox import Toolbox

Run = Callable[..., Any]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str) -> Any:
        code = main(["--vault", str(vault), "--json", *arguments])
        captured = capsys.readouterr()
        return code, json.loads(captured.out or captured.err or "{}")

    return invoke


@pytest.fixture
def prepared(run: Run, repository: Path) -> str:
    """An initialised, mapped and indexed vault, and its workspace id."""
    run("init")
    _, created = run("workspace", "create", "Parity", "--type", "product")
    run("workspace", "map", str(created["id"]), "--repo", str(repository))
    run("index")
    return str(created["id"])


@pytest.fixture
def toolbox(vault: Path) -> Toolbox:
    # `local=True`: these compare the two in-process paths. Whether a daemon
    # answers is tested separately, and a parity test that depended on one
    # running would be a parity test that usually skipped.
    return Toolbox(vault, local=True)


class TestReadParity:
    def test_workspace_resolve(
        self, run: Run, toolbox: Toolbox, prepared: str, repository: Path
    ) -> None:
        _, cli = run("workspace", "resolve", "--path", str(repository))
        assert toolbox.workspace_resolve({"cwd": str(repository)}) == cli

    def test_search(self, run: Run, toolbox: Toolbox, prepared: str) -> None:
        _, cli = run("search", "parity")
        assert toolbox.search({"query": "parity"}) == cli

    def test_search_explaining_itself(self, run: Run, toolbox: Toolbox, prepared: str) -> None:
        """Search explaining itself: one shape, three surfaces."""
        _, cli = run("search", "parity", "--explain")
        assert toolbox.search({"query": "parity", "explain": True}) == cli
        assert all(result["explain"] for result in cli["results"])

    def test_search_with_a_limit(self, run: Run, toolbox: Toolbox, prepared: str) -> None:
        _, cli = run("search", "parity", "--limit", "1")
        assert toolbox.search({"query": "parity", "limit": 1}) == cli

    def test_doctor(self, run: Run, toolbox: Toolbox, prepared: str) -> None:
        _, cli = run("doctor")
        assert toolbox.doctor({}) == cli

    def test_doctor_at_another_level(self, run: Run, toolbox: Toolbox, prepared: str) -> None:
        _, cli = run("doctor", "--level", "core")
        assert toolbox.doctor({"level": "core"}) == cli

    def test_get_concept(self, run: Run, toolbox: Toolbox, prepared: str) -> None:
        _, cli = run("concept", "get", prepared)
        assert toolbox.get_concept({"id": prepared}) == cli

    def test_context_startup(
        self, run: Run, toolbox: Toolbox, prepared: str, repository: Path
    ) -> None:
        # A pack opens a session, so the ids differ by design. Everything else
        # must match, including the *shape*: an absent key and a null key are
        # different things and both surfaces must choose the same one.
        _, cli = run("context", "startup", "--cwd", str(repository))
        mcp = dict(toolbox.context_startup({"cwd": str(repository)}))
        assert set(mcp) == set(cli)
        assert mcp["session_id"] is not None
        assert cli["session_id"] is not None
        del mcp["session_id"], cli["session_id"]
        assert mcp == cli

    def test_context_focus(
        self, run: Run, toolbox: Toolbox, prepared: str, repository: Path
    ) -> None:
        _, cli = run("context", "focus", "parity", "--cwd", str(repository))
        mcp = dict(toolbox.context_focus({"terms": ["parity"], "cwd": str(repository)}))
        del mcp["session_id"], cli["session_id"]
        assert mcp == cli

    def test_context_at_depth(
        self, run: Run, toolbox: Toolbox, prepared: str, repository: Path
    ) -> None:
        _, cli = run("context", "focus", "parity", "--cwd", str(repository), "--depth", "deep")
        mcp = dict(
            toolbox.context_focus({"terms": ["parity"], "cwd": str(repository), "depth": "deep"})
        )
        assert mcp["depth"] == cli["depth"] == "deep"
        del mcp["session_id"], cli["session_id"]
        assert mcp == cli


class TestFailureParity:
    def test_an_unindexed_vault_refuses_through_both(
        self, run: Run, vault: Path, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from never4ga.mcp.server import ToolFailureError

        run("init")
        _, created = run("workspace", "create", "Parity", "--type", "product")
        run("workspace", "map", str(created["id"]), "--repo", str(repository))

        code, cli = run("context", "startup", "--cwd", str(repository))
        assert code != 0
        with pytest.raises(ToolFailureError) as raised:
            Toolbox(vault, local=True).context_startup({"cwd": str(repository)})
        # The same code and the same hint, so a caller learns the same thing
        # whichever surface it asked through.
        assert raised.value.error.code == cli["error"]["code"] == "index_not_built"
        assert raised.value.error.repair_hint == cli["error"]["repair_hint"]

    def test_a_concept_that_is_not_there_refuses_through_both(
        self, run: Run, vault: Path, prepared: str
    ) -> None:
        from never4ga.domain.identity import ConceptId
        from never4ga.mcp.server import ToolFailureError

        missing = str(ConceptId.new())
        code, cli = run("concept", "get", missing)
        assert code != 0
        with pytest.raises(ToolFailureError) as raised:
            Toolbox(vault, local=True).get_concept({"id": missing})
        assert raised.value.error.code == cli["error"]["code"] == "concept_not_found"


class TestTheToolSurface:
    def test_every_tool_is_reachable_and_described(self, toolbox: Toolbox) -> None:
        for tool in toolbox.tools():
            described = tool.describe()
            assert described["name"].startswith("never4ga_")
            assert described["description"].strip()

    def test_the_names_follow_the_contract(self, toolbox: Toolbox) -> None:
        # details/api-cli-mcp-contract.md section 5, whole: eight reads, three
        # safe writes, and four more writes. Two names follow core/04 section 32
        # rather than the contract's older spellings: `closeout` is `wrap` and
        # `record_decision` is `decide`.
        assert {tool.name for tool in toolbox.tools()} == {
            "never4ga_workspace_resolve",
            "never4ga_context_startup",
            "never4ga_context_focus",
            "never4ga_search",
            "never4ga_get_concept",
            "never4ga_work_search",
            "never4ga_work_get",
            "never4ga_doctor",
            "never4ga_capture",
            "never4ga_checkpoint",
            "never4ga_wrap",
            "never4ga_decide",
            "never4ga_work_create",
            "never4ga_work_update",
            "never4ga_work_comment",
        }

    def test_every_write_tool_defaults_to_describing_rather_than_sending(
        self, toolbox: Toolbox
    ) -> None:
        # The tracker-write gate as a schema default. A model that omits
        # `apply` must get a description, not a change to somebody's tracker.
        # `wrap` is not here: it writes nothing to a tracker, so it has
        # nothing to gate.
        gated = {
            "never4ga_work_create",
            "never4ga_work_update",
            "never4ga_work_comment",
        }
        for tool in toolbox.tools():
            if tool.name in gated:
                described = tool.describe()["inputSchema"]
                assert "apply" in described["properties"]
                assert "apply" not in described["required"]

    def test_the_decision_tool_says_it_drafts_rather_than_decides(self, toolbox: Toolbox) -> None:
        # An agent never authors an accepted decision. The tool has to say so,
        # because the description is what a model reads.
        [decide] = [one for one in toolbox.tools() if one.name == "never4ga_decide"]
        assert "PROPOSED" in decide.description
        assert "not in effect" in decide.description


class TestWriteParity:
    """The writes, through both surfaces, against a workspace with no tracker.

    Deliberately without one: what these compare is the *gate*, and the gate is
    the part that must not differ. Whether a real tracker accepts a change is
    for the live OpenProject suite, and a parity test that needed a network
    would be a parity test that usually skipped.
    """

    def test_work_search_refuses_the_same_way(
        self, run: Run, vault: Path, prepared: str, repository: Path
    ) -> None:
        from never4ga.mcp.server import ToolFailureError

        code, cli = run("work", "search", "--path", str(repository))
        assert code != 0
        with pytest.raises(ToolFailureError) as raised:
            Toolbox(vault, local=True).work_search({"cwd": str(repository)})
        assert raised.value.error.code == cli["error"]["code"] == "work_management_undeclared"
        assert raised.value.error.repair_hint == cli["error"]["repair_hint"]

    def test_work_update_refuses_the_same_way(
        self, run: Run, vault: Path, prepared: str, repository: Path
    ) -> None:
        from never4ga.mcp.server import ToolFailureError

        code, cli = run(
            "work", "update", "1", "--field", "status=Closed", "--path", str(repository)
        )
        assert code != 0
        with pytest.raises(ToolFailureError) as raised:
            Toolbox(vault, local=True).work_update(
                {"work_item": "1", "fields": {"status": "Closed"}, "cwd": str(repository)}
            )
        assert raised.value.error.code == cli["error"]["code"]

    def test_capture_writes_the_same_shape(self, run: Run, toolbox: Toolbox, prepared: str) -> None:
        _, cli = run("capture", "a", "thought")
        mcp = toolbox.capture({"text": "a thought"})
        # Two notes, so the paths differ by their timestamps; the *shape* is
        # what parity is about here.
        assert set(mcp) == set(cli)
        assert mcp["title"] == cli["title"] == "a thought"

    def test_wrap_writes_nothing_to_a_tracker(self, toolbox: Toolbox) -> None:
        # Asserted in the tool's own schema, which is where a model learns it.
        # A ticket either needs particular information, which somebody should
        # write, or it does not.
        [wrap] = [one for one in toolbox.tools() if one.name == "never4ga_wrap"]
        assert "ticket" not in wrap.describe()["inputSchema"]["properties"]
        assert "writes nothing but the log" in wrap.description

    def test_a_work_candidate_is_declared_and_reported_never_acted_on(
        self, run: Run, toolbox: Toolbox, prepared: str, repository: Path
    ) -> None:
        pack = toolbox.context_startup({"cwd": str(repository)})
        session = str(pack["session_id"])
        toolbox.checkpoint(
            {
                "session_id": session,
                "note": "did a thing",
                "work": ["840: ready to close"],
            }
        )
        done = toolbox.wrap({"session_id": session, "title": "A session"})
        assert done["work"] == ["840: ready to close"]

    def test_checkpoint_and_wrap_agree_with_the_cli(
        self, run: Run, toolbox: Toolbox, prepared: str, repository: Path
    ) -> None:
        pack = toolbox.context_startup({"cwd": str(repository)})
        session = str(pack["session_id"])
        mcp = toolbox.checkpoint({"session_id": session, "note": "did a thing"})
        assert set(mcp) == {
            "session_id",
            "recorded_at",
            "note",
            "actions",
            "decisions",
            "memories",
            "work",
            "context",
        }
        _, cli = run("wrap", "--session", session, "--title", "A session")
        again = toolbox.wrap({"session_id": session, "title": "A session"})
        # Wrapping is idempotent, so the second one updates the first document
        # rather than writing another, through either surface.
        assert again["id"] == cli["id"]
        assert again["updated"] is True

    def test_decide_drafts_a_proposal_rather_than_a_decision(
        self, toolbox: Toolbox, vault: Path, prepared: str
    ) -> None:
        made = toolbox.decide({"title": "Something to settle", "workspace_id": prepared})
        written = (vault / str(made["path"])).read_text()
        assert "lifecycle: proposed" in written


def _logs_under(vault: Path) -> list[Path]:
    """Every activity log written under a workspace, and no navigation.

    `index.md` lists the documents in its directory *by title*, so a search for
    the file mentioning "A Borrowed Session" matches the log and the index that
    links to it. The index has no `by:` line, and which of the two `rglob`
    yields first is filesystem order, so without this filter a test could pass
    on one machine and raise `StopIteration` on another.
    """
    return [
        path for path in (vault / "10_Workspaces").rglob("Logs/**/*.md") if path.name != "index.md"
    ]


class TestTheToolboxDefaultDefersToTheSession:
    """Whose name a toolbox wrap records, when nobody said.

    `Toolbox` defaults its actor to `mcp/never4ga`, which is right for a write
    that has nothing better: a tool call by a model is not a person typing,
    and `core/02` section 5.2 makes saying so a MUST. It is *not* right when a
    session exists that recorded who actually opened it: `claude-code/
    claude-opus-5` is the same fact, only true. A generic default must not
    outrank an observed identity.

    So the default yields to a session and an explicit `--actor` does not:
    the flag is somebody stating who is writing, and stating it is the only
    way to be more specific than what was observed.
    """

    def _session_and_log(self, run: Run, toolbox: Toolbox, repository: Path, vault: Path) -> str:

        pack = toolbox.context_startup({"cwd": str(repository)})
        session = str(pack["session_id"])
        toolbox.checkpoint({"session_id": session, "note": "did a thing"})
        toolbox.wrap({"session_id": session, "title": "A Toolbox Session"})
        for path in _logs_under(vault):
            text = path.read_text(encoding="utf-8")
            if "A Toolbox Session" in text:
                for line in text.splitlines():
                    if line.strip().startswith("by:"):
                        return line.split("by:", 1)[1].strip()
        raise AssertionError(f"no toolbox log found under {vault}")

    def test_a_session_that_knows_who_opened_it_wins(
        self, run: Run, vault: Path, repository: Path, prepared: str
    ) -> None:
        # The session was opened by this very toolbox carrying the client's
        # own name; the log must say that rather than the generic default.
        toolbox = Toolbox(vault, actor="claude-code/claude-opus-5", local=True)
        assert self._session_and_log(run, toolbox, repository, vault) == "claude-code/claude-opus-5"

    def test_a_session_opened_by_another_client_is_not_relabelled(
        self, run: Run, vault: Path, repository: Path, prepared: str
    ) -> None:
        """The case that actually diverges: two tools, one session.

        A session opened in one tool is visible and joinable in another. So
        the CLI opens one as `codex/gpt-5` and an MCP server started with no
        `--actor` wraps it. The log must not say `mcp/never4ga`, because the
        server is the transport that carried the wrap, not the producer that
        did the work.
        """
        code, pack = run("--actor", "codex/gpt-5", "context", "startup", "--cwd", str(repository))
        assert code == 0
        session = str(pack["session_id"])
        assert main(["--vault", str(vault), "checkpoint", "--session", session, "did a thing"]) == 0
        toolbox = Toolbox(vault, local=True)
        toolbox.wrap({"session_id": session, "title": "A Borrowed Session"})
        for path in _logs_under(vault):
            text = path.read_text(encoding="utf-8")
            if "A Borrowed Session" in text:
                by = next(
                    line.split("by:", 1)[1].strip()
                    for line in text.splitlines()
                    if line.strip().startswith("by:")
                )
                assert by == "codex/gpt-5"
                return
        raise AssertionError("no log written")

    def test_a_session_that_recorded_nobody_does_not_become_a_person(
        self, run: Run, vault: Path, repository: Path, prepared: str
    ) -> None:
        # Deferring blindly would write `human:owner` on a log a model wrote,
        # which is the exact false claim core/02 section 5.2 exists to stop.
        code, pack = run("context", "startup", "--cwd", str(repository))
        assert code == 0
        session = str(pack["session_id"])
        assert main(["--vault", str(vault), "checkpoint", "--session", session, "a thing"]) == 0
        from never4ga.mcp.toolbox import DEFAULT_ACTOR

        Toolbox(vault, local=True).wrap({"session_id": session, "title": "An Anonymous Session"})
        for path in _logs_under(vault):
            text = path.read_text(encoding="utf-8")
            if "An Anonymous Session" in text:
                by = next(
                    line.split("by:", 1)[1].strip()
                    for line in text.splitlines()
                    if line.strip().startswith("by:")
                )
                assert by == DEFAULT_ACTOR
                return
        raise AssertionError("no log written")

    def test_the_built_in_default_still_names_the_server(
        self, run: Run, vault: Path, repository: Path, prepared: str
    ) -> None:
        # Nobody said anything, so `mcp/never4ga` is the honest answer and
        # `human:owner` would be a false claim about authorship.
        from never4ga.mcp.toolbox import DEFAULT_ACTOR

        toolbox = Toolbox(vault, local=True)
        assert self._session_and_log(run, toolbox, repository, vault) == DEFAULT_ACTOR


class _AppliedWriter:
    """A work writer whose every write went through, as a tracker would reply.

    What is under test is the toolbox's bookkeeping after a write, not the
    tracker round trip, which `test_cli_work.py` and the adapter tests cover.
    """

    def update(self, bound: Any, work_item: str, fields: Any) -> dict[str, Any]:
        return {"applied": True, "changed": dict(fields)}

    def create(self, bound: Any, fields: Any) -> dict[str, Any]:
        return {"applied": True, "item": {"ref": "840", "url": "https://pm/840"}}

    def comment(
        self, bound: Any, work_item: str, body: str, *, amends: str | None = None
    ) -> dict[str, Any]:
        return {"applied": True, "url": f"https://pm/{work_item}"}


WRITES = [
    ("work_comment", {"work_item": "840", "body": "done"}),
    ("work_update", {"work_item": "840", "fields": {"status": "Closed"}}),
    ("work_create", {"title": "A new item"}),
]


class TestAWriteThroughMcpIsRecordedAgainstItsSession:
    """The MCP work tools record a write against a session, so wrap can see it.

    An agent that declares items with `checkpoint --work` and comments on them
    through `never4ga_work_comment` must not have `wrap` report them as never
    written to. The CLI records the write through `--session`; the MCP work
    tools take a session for the same reason.
    """

    @pytest.mark.parametrize(
        "name", ["never4ga_work_create", "never4ga_work_update", "never4ga_work_comment"]
    )
    def test_every_work_write_takes_the_session(self, toolbox: Toolbox, name: str) -> None:
        [tool] = [one for one in toolbox.tools() if one.name == name]
        assert "session_id" in tool.describe()["inputSchema"]["properties"]

    @pytest.mark.parametrize(("verb", "arguments"), WRITES)
    def test_a_declared_item_written_through_mcp_is_not_outstanding(
        self,
        toolbox: Toolbox,
        prepared: str,
        repository: Path,
        monkeypatch: pytest.MonkeyPatch,
        verb: str,
        arguments: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(toolbox, "_bound_writer", lambda _: (_AppliedWriter(), None))
        session = str(toolbox.context_startup({"cwd": str(repository)})["session_id"])
        toolbox.checkpoint({"session_id": session, "note": "acting", "work": ["840: ready"]})

        getattr(toolbox, verb)(
            {**arguments, "cwd": str(repository), "apply": True, "session_id": session}
        )

        done = toolbox.wrap({"session_id": session, "title": "A session"})
        assert done["outstanding_work"] == []

    def test_a_write_that_names_no_session_is_still_outstanding(
        self,
        toolbox: Toolbox,
        prepared: str,
        repository: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The observation is only as good as what it was told: a write with
        # no session belongs to none, and wrap is right to say so.
        monkeypatch.setattr(toolbox, "_bound_writer", lambda _: (_AppliedWriter(), None))
        session = str(toolbox.context_startup({"cwd": str(repository)})["session_id"])
        toolbox.checkpoint({"session_id": session, "note": "acting", "work": ["840: ready"]})

        toolbox.work_comment(
            {"work_item": "840", "body": "done", "cwd": str(repository), "apply": True}
        )

        done = toolbox.wrap({"session_id": session, "title": "A session"})
        assert done["outstanding_work"] == ["840"]


class TestAnAgentIsToldToRetryATrackerThatDidNotAnswer:
    """A tracker that did not answer is retryable, on the surface an agent reads.

    "openproject does not support 'comment_work_item'" while the instance is
    down would tell the agent to give up on the write. The tool fails with the
    read side's `tracker_unreachable`, retryable.
    """

    @pytest.mark.parametrize(("verb", "arguments"), WRITES)
    def test_every_write_says_the_tracker_did_not_answer(
        self,
        toolbox: Toolbox,
        prepared: str,
        repository: Path,
        monkeypatch: pytest.MonkeyPatch,
        verb: str,
        arguments: dict[str, Any],
    ) -> None:
        from never4ga.mcp.server import ToolFailureError
        from tests.openproject_fixtures import DownTracker

        down = DownTracker()
        monkeypatch.setattr(toolbox, "_bound_writer", lambda _: (down, down.bind()))

        with pytest.raises(ToolFailureError) as failure:
            getattr(toolbox, verb)({**arguments, "cwd": str(repository), "apply": True})

        assert failure.value.error.code == "tracker_unreachable"
        assert failure.value.error.retryable is True


class TestAnAgentIsNotToldToRetryAWriteThatLanded:
    """A write that landed is not reported as failed when recording it fails.

    A locked or corrupt ``sessions.sqlite3`` must not raise out of a tool call
    **after** the tracker has accepted the write. The agent would be told the
    write had failed, and an agent retries a failed write. For `work_create`
    that retry is a second item; for `work_comment`, a second comment.
    """

    @pytest.fixture
    def unusable(self, monkeypatch: pytest.MonkeyPatch, toolbox: Toolbox) -> Toolbox:
        """The real ``_record_write``, over a store that cannot be used.

        Patched where the toolbox reaches for it rather than over the method
        itself, so what runs is the wiring under test: a lock held past the
        busy timeout, a full disk and a corrupt file all arrive here as the
        port's declared error.
        """
        from never4ga.errors import SessionStoreError

        class _Unusable:
            def __init__(self, _database: Any) -> None:
                pass

            def record_work_action(self, action: Any) -> None:
                raise SessionStoreError("database is locked")

            def __getattr__(self, name: str) -> Any:
                raise SessionStoreError("database is locked")

        monkeypatch.setattr(toolbox, "_bound_writer", lambda _: (_AppliedWriter(), None))
        monkeypatch.setattr("never4ga.mcp.toolbox.FileSessionStore", _Unusable)
        return toolbox

    @pytest.mark.parametrize(("verb", "arguments"), WRITES)
    def test_the_tool_call_succeeds(
        self,
        unusable: Toolbox,
        prepared: str,
        repository: Path,
        verb: str,
        arguments: dict[str, Any],
    ) -> None:
        answer = getattr(unusable, verb)(
            {
                **arguments,
                "cwd": str(repository),
                "apply": True,
                "session_id": "01a0c1be-f64a-76b0-8c96-bff5b4a95382",
            }
        )

        assert answer["applied"] is True

    def test_the_answer_says_the_record_was_lost(
        self, unusable: Toolbox, prepared: str, repository: Path
    ) -> None:
        # The agent needs to be able to tell "it landed, the bookkeeping did
        # not" from "it failed".
        answer = unusable.work_create(
            {
                "title": "A new item",
                "cwd": str(repository),
                "apply": True,
                "session_id": "01a0c1be-f64a-76b0-8c96-bff5b4a95382",
            }
        )

        assert "database is locked" in answer["record_lost"]

    @pytest.mark.parametrize(("verb", "arguments"), WRITES)
    def test_an_ordinary_write_says_nothing_about_a_record(
        self,
        toolbox: Toolbox,
        prepared: str,
        repository: Path,
        monkeypatch: pytest.MonkeyPatch,
        verb: str,
        arguments: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(toolbox, "_bound_writer", lambda _: (_AppliedWriter(), None))
        session = str(toolbox.context_startup({"cwd": str(repository)})["session_id"])

        answer = getattr(toolbox, verb)(
            {**arguments, "cwd": str(repository), "apply": True, "session_id": session}
        )

        assert "record_lost" not in answer


class TestDecisionNumbering:
    """`concept create` and `never4ga_decide` number decisions alike.

    The rule is core/02 section 21.11.
    """

    def test_both_take_the_next_number_in_the_folder(
        self, run: Run, toolbox: Toolbox, prepared: str, vault: Path
    ) -> None:
        decisions = vault / "10_Workspaces/Parity/Decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        (decisions / "adr-0004_by-hand.md").write_text("# By hand\n", encoding="utf-8")

        _, from_cli = run("concept", "create", "decision", "From CLI", "--workspace", prepared)
        from_mcp = toolbox.decide({"title": "From MCP", "workspace_id": prepared})

        assert from_cli["path"] == "10_Workspaces/Parity/Decisions/adr-0005_from-cli.md"
        assert from_mcp["path"] == "10_Workspaces/Parity/Decisions/adr-0006_from-mcp.md"

    def test_both_can_ask_for_a_first_number_and_name_a_series(
        self, run: Run, toolbox: Toolbox, prepared: str
    ) -> None:
        _, from_cli = run(
            "concept", "create", "decision", "From CLI", "--workspace", prepared, "--number"
        )
        from_mcp = toolbox.decide({"title": "From MCP", "workspace_id": prepared, "series": "ops"})

        assert from_cli["path"] == "10_Workspaces/Parity/Decisions/adr-0001_from-cli.md"
        assert from_mcp["path"] == "10_Workspaces/Parity/Decisions/adr-ops-0001_from-mcp.md"
