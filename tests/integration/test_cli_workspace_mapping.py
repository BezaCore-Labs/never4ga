"""`never4ga workspace map|unmap|mappings|resolve`.

These verbs are the machine-local half of workspace resolution. They stay in
process even when a service is running: the mappings are durable machine-local
state rather than derived state the service owns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from never4ga.context.budget import deep_budget
from never4ga.domain.identity import SessionId
from never4ga.ports.repository_locator import MARKER_FILENAME


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out)


Run = Callable[..., Result]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        argv = ["--vault", str(vault), *(["--json"] if as_json else []), *arguments]
        code = main(argv)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "never4ga"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    return root


@pytest.fixture
def workspace(run: Run) -> str:
    run("init")
    created = run("workspace", "create", "Never4gA", "--type", "product", as_json=True)
    return str(created.json["id"])


class TestMapping:
    def test_mapping_a_repository_reports_what_it_mapped(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        result = run("workspace", "map", workspace, "--repo", str(repository), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["workspace"] == workspace
        assert result.json["repository_root"] == str(repository)

    def test_a_mapping_survives_into_the_next_invocation(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        listed = run("workspace", "mappings", as_json=True)
        assert [m["workspace"] for m in listed.json["mappings"]] == [workspace]

    def test_mapping_records_the_workspace_path_from_the_vault(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        # The user names a workspace; where it lives in the vault is looked up
        # rather than typed.
        result = run("workspace", "map", workspace, "--repo", str(repository), as_json=True)
        assert result.json["workspace_path"].endswith("workspace.md")

    def test_mapping_an_unknown_workspace_fails(self, run: Run, repository: Path) -> None:
        run("init")
        result = run(
            "workspace",
            "map",
            "01a02c26-f966-7190-a245-b795880e79d3",
            "--repo",
            str(repository),
        )
        assert result.code == EXIT_FAILED

    def test_mapping_a_directory_that_is_not_a_repository_fails(
        self, run: Run, workspace: str, tmp_path: Path
    ) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert run("workspace", "map", workspace, "--repo", str(plain)).code == EXIT_FAILED

    def test_unmapping_removes_it(self, run: Run, workspace: str, repository: Path) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        assert run("workspace", "unmap", workspace).code == EXIT_OK
        assert run("workspace", "mappings", as_json=True).json["mappings"] == []

    def test_unmapping_something_unmapped_fails(self, run: Run, workspace: str) -> None:
        assert run("workspace", "unmap", workspace).code == EXIT_FAILED

    def test_mappings_are_empty_before_anything_is_mapped(self, run: Run) -> None:
        run("init")
        assert run("workspace", "mappings", as_json=True).json["mappings"] == []


class TestResolving:
    def test_a_path_inside_a_mapped_repo_resolves(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("workspace", "resolve", "--path", str(repository / "src"), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["workspace"] == workspace

    def test_resolving_reports_why(self, run: Run, workspace: str, repository: Path) -> None:
        # core/07 section 12: every acquisition says what put it there.
        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("workspace", "resolve", "--path", str(repository), as_json=True)
        assert result.json["reason"]["code"]

    def test_an_unmapped_repo_does_not_guess(self, run: Run, repository: Path) -> None:
        run("init")
        result = run("workspace", "resolve", "--path", str(repository))
        assert result.code == EXIT_FAILED

    def test_a_conflicting_marker_is_reported_and_the_registry_wins(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        (repository / MARKER_FILENAME).write_text(
            'workspace = "01a02c26-f966-7190-a245-b795880e79d3"\n'
        )
        result = run("workspace", "resolve", "--path", str(repository), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["workspace"] == workspace
        assert result.json["conflicts"]

    def test_resolution_without_a_conflict_reports_none(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("workspace", "resolve", "--path", str(repository), as_json=True)
        assert result.json["conflicts"] == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestAWorktree:
    """A worktree of a mapped repository resolves to that repository's workspace,
    and `context startup` returns a pack from inside it.
    """

    @pytest.fixture
    def main_checkout(self, tmp_path: Path) -> Path:
        root = tmp_path / "Projects" / "dev-environment"
        root.mkdir(parents=True)
        for arguments in (
            ("init", "--initial-branch", "main"),
            ("config", "user.email", "test@example.invalid"),
            ("config", "user.name", "Test"),
            ("commit", "--allow-empty", "-m", "Start"),
        ):
            subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)
        return root

    @pytest.fixture
    def worktree(self, main_checkout: Path, tmp_path: Path) -> Path:
        # Somewhere unrelated, as an orchestrator puts them.
        root = tmp_path / "cache" / "worktrees" / "task-1"
        subprocess.run(
            ["git", "-C", str(main_checkout), "worktree", "add", "-b", "task-1", str(root)],
            check=True,
            capture_output=True,
        )
        return root

    def test_a_worktree_resolves_to_the_repository_s_workspace(
        self, run: Run, workspace: str, main_checkout: Path, worktree: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(main_checkout))
        result = run("workspace", "resolve", "--path", str(worktree), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["workspace"] == workspace

    def test_startup_returns_a_pack_from_inside_a_worktree(
        self, run: Run, workspace: str, main_checkout: Path, worktree: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(main_checkout))
        run("index")
        result = run("context", "startup", "--path", str(worktree), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["items"]

    def test_the_worktree_is_what_resolution_reports_as_the_repository(
        self, run: Run, workspace: str, main_checkout: Path, worktree: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(main_checkout))
        result = run("workspace", "resolve", "--path", str(worktree), as_json=True)
        assert result.json["repository_root"] == str(worktree)

    def test_the_pack_s_git_signals_describe_the_worktree(
        self, run: Run, workspace: str, main_checkout: Path, worktree: Path
    ) -> None:
        """The pack's git signals describe the worktree, not the main checkout.

        The branch is the whole point: an agent working in `task-1` whose pack
        says `main` has been told the one thing the signal exists to say, wrongly.
        """
        run("workspace", "map", workspace, "--repo", str(main_checkout))
        run("index")
        result = run("context", "startup", "--path", str(worktree), as_json=True)
        signals = {signal["kind"]: signal["value"] for signal in result.json["signals"]}
        assert signals["git.branch"] == "task-1"
        assert result.json["scope"]["repository_root"] == str(worktree)


class TestContextVerbs:
    def test_a_startup_pack_is_produced(self, run: Run, workspace: str, repository: Path) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run("context", "startup", "--path", str(repository), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["depth"] == "startup"
        assert result.json["scope"]["workspace_id"] == workspace

    def test_every_item_says_why_it_is_there(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run("context", "startup", "--path", str(repository), as_json=True)
        assert result.json["items"]
        assert all(item["reason"]["code"] for item in result.json["items"])

    def test_git_state_reaches_the_pack(self, run: Run, workspace: str, repository: Path) -> None:
        import shutil
        import subprocess

        if shutil.which("git") is None:
            pytest.skip("git is not installed")
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run("context", "startup", "--path", str(repository), as_json=True)
        assert any(signal["kind"].startswith("git.") for signal in result.json["signals"])

    def test_a_focused_pack_takes_terms(self, run: Run, workspace: str, repository: Path) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run("context", "focus", "never4ga", "--path", str(repository), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["depth"] == "focused"

    def test_a_deep_pack_is_reached_through_depth(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        # core/04 section 16's own example: `context startup --depth deep`.
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run(
            "context", "startup", "--path", str(repository), "--depth", "deep", as_json=True
        )
        assert result.code == EXIT_OK
        assert result.json["depth"] == "deep"

    def test_a_deep_pack_is_at_least_as_wide_as_a_startup_one(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        startup = run("context", "startup", "--path", str(repository), as_json=True)
        deep = run("context", "startup", "--path", str(repository), "--depth", "deep", as_json=True)
        assert len(deep.json["items"]) >= len(startup.json["items"])
        # Wider, and still bounded: core/07 section 10 admits no depth at which
        # a pack stops having a ceiling.
        assert len(deep.json["items"]) <= deep_budget().max_items

    def test_the_client_that_asked_is_recorded(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        # core/04 section 16: `never4ga context startup --client claude-code`.
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run(
            "context",
            "startup",
            "--cwd",
            str(repository),
            "--client",
            "claude-code",
            as_json=True,
        )
        assert result.code == EXIT_OK
        assert result.json["client"] == "claude-code"

    def test_a_task_is_recorded_as_given_and_not_as_said(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        # The rule that makes `--task` safe: the pack says a task was named,
        # never what it named. core/04 section 16 forbids persisting the raw
        # prompt merely because retrieval used it.
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run(
            "context",
            "startup",
            "--path",
            str(repository),
            "--task",
            "Implement member search for the directory",
            as_json=True,
        )
        assert result.json["task_given"] is True
        assert "Implement member search" not in result.out

    def test_no_task_is_no_task(self, run: Run, workspace: str, repository: Path) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run("context", "startup", "--path", str(repository), as_json=True)
        assert result.json["task_given"] is False
        assert result.json["client"] is None

    def test_an_unmapped_location_does_not_guess(self, run: Run, repository: Path) -> None:
        run("init")
        run("index")
        assert run("context", "startup", "--path", str(repository)).code == EXIT_FAILED

    def test_the_budget_is_honoured(self, run: Run, workspace: str, repository: Path) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run(
            "context", "startup", "--path", str(repository), "--max-items", "1", as_json=True
        )
        assert len(result.json["items"]) == 1

    def test_no_llm_stage_runs(self, run: Run, workspace: str, repository: Path) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        result = run("context", "startup", "--path", str(repository), as_json=True)
        assert result.json["llm_stages"] == []


class TestStartupMintsASession:
    """core/04 section 17 puts `session_id` at the top of a startup response.

    Never4gA mints it. The identity has to exist before checkpoints or a wrap
    can be grouped by it.
    """

    def _startup(self, run: Run, workspace: str, repository: Path) -> dict[str, object]:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        payload = run("context", "startup", "--path", str(repository), as_json=True).json
        assert isinstance(payload, dict)
        return payload

    def test_a_startup_pack_carries_a_session_id(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        payload = self._startup(run, workspace, repository)
        raw = str(payload["session_id"])
        assert str(SessionId.parse(raw)) == raw

    def test_two_startups_are_two_sessions(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        first = self._startup(run, workspace, repository)["session_id"]
        second = self._startup(run, workspace, repository)["session_id"]
        assert first != second

    def test_sessions_sort_by_time(self, run: Run, workspace: str, repository: Path) -> None:
        minted = [
            SessionId.parse(str(self._startup(run, workspace, repository)["session_id"]))
            for _ in range(5)
        ]
        assert sorted(minted) == minted

    def test_the_human_output_names_it(self, run: Run, workspace: str, repository: Path) -> None:
        # An agent reading the plain output has to be able to find the id it is
        # expected to pass back to `checkpoint`.
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        assert "session:" in run("context", "startup", "--path", str(repository)).out

    def test_focused_context_does_not_mint_one(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        # core/04 section 34: startup runs once per session; focused retrieval
        # runs many times within it. Minting there would make every retrieval
        # look like a new session.
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        payload = run("context", "focus", "thing", "--path", str(repository), as_json=True).json
        assert payload["session_id"] is None


class TestCheckpoint:
    """`never4ga checkpoint`.

    A checkpoint accumulates outside the vault. The vault is what `wrap`
    writes to, once, at the end.
    """

    def _session(self, run: Run, workspace: str, repository: Path) -> str:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        payload = run("context", "startup", "--path", str(repository), as_json=True).json
        assert isinstance(payload, dict)
        return str(payload["session_id"])

    def test_a_startup_opens_a_session_a_checkpoint_can_join(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        assert run("checkpoint", "did a thing", "--session", session).code == EXIT_OK

    def test_repeated_checkpoints_accumulate_in_order(
        self, run: Run, workspace: str, repository: Path, vault: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        for index in range(3):
            run("checkpoint", f"step {index}", "--session", session)

        from never4ga.adapters.sqlite.session_store import SQLiteSessionStore
        from never4ga.adapters.sqlite.sessions import open_sessions
        from never4ga.domain.identity import ConceptId
        from never4ga.platform_paths import PlatformPaths

        vault_id = ConceptId.parse(run("status", as_json=True).json["vault_id"])
        connection = open_sessions(PlatformPaths.resolve().sessions_database(vault_id))
        try:
            store = SQLiteSessionStore(connection)
            notes = [c.note for c in store.checkpoints(SessionId.parse(session))]
        finally:
            connection.close()
        assert notes == ["step 0", "step 1", "step 2"]

    def test_an_unknown_session_is_refused_rather_than_invented(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        self._session(run, workspace, repository)
        stranger = str(SessionId.new())
        result = run("checkpoint", "orphan", "--session", stranger)
        assert result.code != EXIT_OK
        output = result.out + result.err
        assert "never opened" in output
        # And the hint points at the tool that fixes it. The generic handler
        # suggests `doctor`, which inspects the vault; a session is not there.
        assert "context startup" in output
        assert "doctor" not in output

    def test_a_malformed_session_is_refused_with_a_hint(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        self._session(run, workspace, repository)
        result = run("checkpoint", "nope", "--session", "not-a-uuid")
        assert result.code != EXIT_OK

    def test_the_vault_is_untouched_by_any_number_of_checkpoints(
        self, run: Run, workspace: str, repository: Path, vault: Path
    ) -> None:
        # Stated as a property: the vault after twenty checkpoints is
        # byte-identical to the vault before them.
        session = self._session(run, workspace, repository)
        before = _vault_fingerprint(vault)
        for index in range(20):
            run("checkpoint", f"step {index}", "--session", session)
        assert _vault_fingerprint(vault) == before

    def test_actions_are_recorded_beside_the_note(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        payload = run(
            "checkpoint",
            "cleared two defects",
            "--session",
            session,
            "--action",
            "merged #37",
            "--action",
            "wrote a plan",
            as_json=True,
        ).json
        assert payload["actions"] == ["merged #37", "wrote a plan"]


def _vault_fingerprint(vault: Path) -> dict[str, bytes]:
    """Every file in the vault and its exact bytes."""
    import hashlib

    return {
        str(path.relative_to(vault)): hashlib.sha256(path.read_bytes()).digest()
        for path in sorted(vault.rglob("*"))
        if path.is_file()
    }


class TestWrapVerb:
    """`never4ga wrap`, through the CLI."""

    def _session(self, run: Run, workspace: str, repository: Path) -> str:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        payload = run("context", "startup", "--path", str(repository), as_json=True).json
        assert isinstance(payload, dict)
        return str(payload["session_id"])

    def test_it_writes_a_log_the_vault_accepts(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        run("checkpoint", "Cleared two defects", "--session", session)
        result = run("wrap", "--session", session, as_json=True)
        assert result.code == EXIT_OK
        assert "/Logs/" in str(result.json["path"])
        # The vault it just wrote to must still be healthy: a log Never4gA
        # wrote and then reports as broken would be a poor first impression.
        assert run("index").code == EXIT_OK
        assert run("doctor", as_json=True).json["healthy"] is True

    def test_wrapping_twice_updates_one_document(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        run("checkpoint", "First pass", "--session", session)
        first = run("wrap", "--session", session, as_json=True).json
        run("checkpoint", "Second pass", "--session", session)
        second = run("wrap", "--session", session, as_json=True).json
        assert (second["id"], second["path"]) == (first["id"], first["path"])
        assert second["updated"] is True

    def test_a_session_with_nothing_recorded_is_refused(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        result = run("wrap", "--session", session)
        assert result.code != EXIT_OK
        assert "no checkpoints" in (result.out + result.err)

    def test_a_session_whose_workspace_is_gone_is_told_where_to_send_the_log(
        self, run: Run, workspace: str, repository: Path, vault: Path
    ) -> None:
        # A session that restructures its workspace out of existence must
        # still be able to close, so the error says where the log can go
        # rather than naming a uuid and stopping.
        import shutil

        session = self._session(run, workspace, repository)
        run("checkpoint", "Moved everything under an area", "--session", session)
        shutil.rmtree(vault / "10_Workspaces" / "Never4gA")
        result = run("wrap", "--session", session)
        assert result.code == EXIT_FAILED
        printed = result.out + result.err
        assert "no longer in the vault" in printed
        assert "--into" in printed

    def test_the_log_can_be_sent_into_a_life_area(
        self, run: Run, workspace: str, repository: Path, vault: Path
    ) -> None:
        import shutil

        session = self._session(run, workspace, repository)
        run("checkpoint", "Never4gA becomes a life area, somehow", "--session", session)
        area = str(run("life-area", "create", "Education", as_json=True).json["id"])
        shutil.rmtree(vault / "10_Workspaces" / "Never4gA")
        result = run("wrap", "--session", session, "--into", area, as_json=True)
        assert result.code == EXIT_OK
        assert str(result.json["path"]).startswith("20_Life/Education/Logs/")
        assert result.json["redirected_from"] == workspace
        assert run("index").code == EXIT_OK
        assert run("doctor", as_json=True).json["healthy"] is True

    def test_a_title_can_be_given(self, run: Run, workspace: str, repository: Path) -> None:
        session = self._session(run, workspace, repository)
        run("checkpoint", "some rambling note that would make a poor title", "--session", session)
        payload = run(
            "wrap", "--session", session, "--title", "Milestone 7 Wrap", as_json=True
        ).json
        assert str(payload["path"]).endswith("_milestone-7-wrap.md")
        assert payload["renamed_from"] is None

    def test_a_second_title_retitles_and_moves_the_log(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        # The whole vault has to survive the move, not just the document:
        # `doctor` is what says the year index followed the file rather than
        # keeping a link to the name it used to have.
        session = self._session(run, workspace, repository)
        run("checkpoint", "First pass", "--session", session)
        first = run("wrap", "--session", session, "--title", "Early Reading", as_json=True).json
        run("checkpoint", "Second pass", "--session", session)
        second = run(
            "wrap", "--session", session, "--title", "What It Turned Out To Be", as_json=True
        ).json

        assert second["id"] == first["id"]
        assert str(second["path"]).endswith("_what-it-turned-out-to-be.md")
        assert second["renamed_from"] == first["path"]
        assert run("index").code == EXIT_OK
        assert run("doctor", as_json=True).json["healthy"] is True

    def test_the_move_is_named_in_the_output(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        run("checkpoint", "First pass", "--session", session)
        run("wrap", "--session", session, "--title", "Early Reading")
        run("checkpoint", "Second pass", "--session", session)
        out = run("wrap", "--session", session, "--title", "Later Reading").out
        assert "_early-reading.md" in out
        assert "moved" in out.casefold()

    def test_the_actor_reaches_the_log(
        self, run: Run, workspace: str, repository: Path, vault: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        payload = run(
            "--actor",
            "codex/gpt-5",
            "context",
            "startup",
            "--path",
            str(repository),
            as_json=True,
        ).json
        session = str(payload["session_id"])
        run("checkpoint", "Did the thing", "--session", session)
        written = run("wrap", "--session", session, as_json=True).json
        text = (vault / str(written["path"])).read_text(encoding="utf-8")
        assert "by: codex/gpt-5" in text


class TestOneClientSeesAnother:
    """A checkpoint written by one client reaches another client's startup pack."""

    def _startup(self, run: Run, repository: Path, client: str) -> dict[str, Any]:
        payload = run(
            "--actor",
            f"{client}/model",
            "context",
            "startup",
            "--client",
            client,
            "--path",
            str(repository),
            as_json=True,
        ).json
        assert isinstance(payload, dict)
        return payload

    def _signals(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        signals = payload["signals"]
        assert isinstance(signals, list)
        return [s for s in signals if s["kind"] == "session.recent"]

    def test_a_claude_checkpoint_reaches_a_codex_startup(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")

        claude = self._startup(run, repository, "claude-code")
        run("checkpoint", "fixed the stale index", "--session", str(claude["session_id"]))

        codex = self._startup(run, repository, "codex")
        (signal,) = self._signals(codex)
        entries = signal["value"]
        assert isinstance(entries, list)
        assert entries[0]["client"] == "claude-code"
        assert "fixed the stale index" in entries[0]["checkpoints"]

    def test_neither_client_had_to_pass_a_session_id(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        # Two tools on one machine have no channel
        # between them, so the answer cannot require one to know the other's id.
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        claude = self._startup(run, repository, "claude-code")
        run("checkpoint", "did a thing", "--session", str(claude["session_id"]))

        codex = self._startup(run, repository, "codex")
        assert str(claude["session_id"]) != str(codex["session_id"])
        assert self._signals(codex)

    def test_a_wrapped_session_is_left_to_its_log(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        claude = self._startup(run, repository, "claude-code")
        session = str(claude["session_id"])
        run("checkpoint", "did a thing", "--session", session)
        run("wrap", "--session", session)
        run("index")

        codex = self._startup(run, repository, "codex")
        assert self._signals(codex) == []

    def test_the_signal_says_why_it_is_there(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        claude = self._startup(run, repository, "claude-code")
        run("checkpoint", "did a thing", "--session", str(claude["session_id"]))
        (signal,) = self._signals(self._startup(run, repository, "codex"))
        assert signal["reason"]["code"] == "recent_activity"


class TestDeclaringDurableThings:
    """Through the CLI: the agent declares, Never4gA records."""

    def _session(self, run: Run, workspace: str, repository: Path) -> str:
        run("workspace", "map", workspace, "--repo", str(repository))
        run("index")
        payload = run("context", "startup", "--path", str(repository), as_json=True).json
        assert isinstance(payload, dict)
        return str(payload["session_id"])

    def test_a_declaration_survives_to_the_wrap(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        run(
            "checkpoint",
            "scoped it",
            "--session",
            session,
            "--decision",
            "session ids are minted by Never4gA",
            "--memory",
            "Actions lags by about fifteen minutes",
        )
        payload = run("wrap", "--session", session, as_json=True).json
        assert payload["decisions"] == ["session ids are minted by Never4gA"]
        assert payload["memories"] == ["Actions lags by about fifteen minutes"]

    def test_wrap_names_the_verb_rather_than_running_it(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        run("checkpoint", "did a thing", "--session", session, "--decision", "a ruling")
        out = run("wrap", "--session", session).out
        assert "none written" in out
        assert "concept create decision" in out

    def test_nothing_reaches_the_decisions_folder(
        self, run: Run, workspace: str, repository: Path, vault: Path
    ) -> None:
        # An agent never authors an accepted ADR, and a `wrap` that drafted
        # one would do exactly that.
        session = self._session(run, workspace, repository)
        run("checkpoint", "did a thing", "--session", session, "--decision", "a big ruling")
        run("wrap", "--session", session)
        decisions = list(vault.rglob("Decisions/*.md"))
        assert decisions == []

    def test_the_log_the_vault_keeps_is_still_healthy(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        session = self._session(run, workspace, repository)
        run("checkpoint", "did a thing", "--session", session, "--decision", "a ruling")
        run("wrap", "--session", session)
        run("index")
        assert run("doctor", as_json=True).json["healthy"] is True


class TestAdoptionIsRecorded:
    """`adapters sync --adopt` records the origin, adoption date and hash of an
    unmanaged file it replaces, in the machine-local extension registry beside
    the ownership manifest."""

    def test_adopting_a_hand_written_file_leaves_a_record(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        from never4ga.adapters.filesystem import ExtensionRegistryFile
        from never4ga.platform_paths import PlatformPaths

        run("workspace", "map", workspace, "--repo", str(repository))
        (repository / "AGENTS.md").write_text("# Somebody's own contract\n")
        result = run("adapters", "sync", "--adopt", "--apply", "--repositories", as_json=True)
        assert result.code == EXIT_OK
        assert {"repository": str(repository), "file": "AGENTS.md"} in result.json[
            "adoptions_recorded"
        ]
        registry = ExtensionRegistryFile(PlatformPaths.resolve().extensions_file)
        record = registry.get(f"pointer:{repository}/AGENTS.md")
        assert record is not None
        assert record.origin == f"{repository}/AGENTS.md"
        assert record.adopted_at
        assert record.replaced_hash
        assert record.deployment_mode.value == "adopted"

    def test_a_plain_sync_records_nothing(self, run: Run, workspace: str, repository: Path) -> None:
        from never4ga.platform_paths import PlatformPaths

        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("adapters", "sync", "--apply", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["adoptions_recorded"] == []
        assert not PlatformPaths.resolve().extensions_file.exists()


class TestRepositoryWritesAreAskedForByName:
    """`adapters sync --apply` deploys to agent clients only. Writing into a
    mapped repository needs `--repositories` as well (details/security-configuration.md
    section 7.1)."""

    def test_apply_alone_writes_nothing_into_the_repository(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("adapters", "sync", "--apply", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["pointers_written"] == []
        assert result.json["repositories_applied"] is False
        assert not (repository / "AGENTS.md").exists()
        assert not (repository / ".gitignore").exists()

    def test_asking_for_repositories_writes_the_pointers(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("adapters", "sync", "--apply", "--repositories", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["repositories_applied"] is True
        assert (repository / "AGENTS.md").is_file()
        assert (repository / ".gitignore").is_file()

    def test_the_dry_run_still_shows_the_repository_plan(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("adapters", "sync", as_json=True)
        assert any(p["file"] == "AGENTS.md" for p in result.json["pointers"])
        assert result.json["repositories_need"] == "--repositories"

    def test_adopt_without_repositories_is_refused(
        self, run: Run, workspace: str, repository: Path
    ) -> None:
        run("workspace", "map", workspace, "--repo", str(repository))
        result = run("adapters", "sync", "--adopt", "--apply")
        assert result.code == EXIT_USAGE
