"""The path a brand-new vault takes, before anything has been indexed.

`init` then `startup` is the first thing anybody does. At that point the derived
directory does not exist yet, and SQLite cannot open a database inside it, so
`startup` must refuse with a structured error rather than raise
``sqlite3.OperationalError``.

Specification:
- details/api-cli-mcp-contract.md section 12 -- a failure is a code, a message
  and a repair hint, never a traceback.
- core/05 section 7 -- asking where derived state belongs must not create it,
  which is why the fix is a check rather than a `mkdir`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import main

Run = Callable[..., Any]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str) -> Any:
        code = main(["--vault", str(vault), "--json", *arguments])
        captured = capsys.readouterr()
        return code, json.loads(captured.out or captured.err or "{}")

    return invoke


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    return root


class TestStartupBeforeAnyIndex:
    def test_it_refuses_structurally_rather_than_raising(self, run: Run, repository: Path) -> None:
        run("init")
        _, created = run("workspace", "create", "Example", "--type", "product")
        run("workspace", "map", str(created["id"]), "--repo", str(repository))

        code, payload = run("context", "startup", "--cwd", str(repository))
        assert code != 0
        assert payload["error"]["code"] == "index_not_built"
        assert "never4ga index" in (payload["error"]["repair_hint"] or "")

    def test_it_does_not_bring_the_database_into_existence(
        self, run: Run, repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # core/05 section 7: asking must not create. Creating the directory to
        # avoid the error would break that rule.
        state = tmp_path / "state"
        monkeypatch.setenv("XDG_DATA_HOME", str(state))
        run("init")
        _, created = run("workspace", "create", "Example", "--type", "product")
        run("workspace", "map", str(created["id"]), "--repo", str(repository))
        run("context", "startup", "--cwd", str(repository))
        assert not list(state.rglob("index.sqlite3"))

    def test_an_index_makes_it_work(self, run: Run, repository: Path) -> None:
        run("init")
        _, created = run("workspace", "create", "Example", "--type", "product")
        run("workspace", "map", str(created["id"]), "--repo", str(repository))
        run("index")
        code, payload = run("context", "startup", "--cwd", str(repository))
        assert code == 0
        assert payload["session_id"]

    @pytest.mark.parametrize("depth", ["startup", "focused", "deep"])
    def test_every_depth_refuses_the_same_way(self, run: Run, repository: Path, depth: str) -> None:
        # All three depths reach the same assembler, so all three must refuse.
        run("init")
        _, created = run("workspace", "create", "Example", "--type", "product")
        run("workspace", "map", str(created["id"]), "--repo", str(repository))
        code, payload = run(
            "context", "focus", "anything", "--cwd", str(repository), "--depth", depth
        )
        assert code != 0
        assert payload["error"]["code"] == "index_not_built"


class TestLogPlacement:
    """`Logs/` has two meanings that differ only by depth (core/01 section 13).

    A dated record lives in `Logs/<YYYY>/`; a closed year's roll-up lives
    directly in `Logs/` as `<YYYY>_summary.md`, keeping the name it was given.
    Both are `type: activity_log`, so the folder is the only thing that tells
    them apart -- which means the reason a document went somewhere has to say
    which of the two just happened.
    """

    def _workspace(self, run: Run) -> str:
        run("init")
        _, created = run("workspace", "create", "Example", "--type", "product")
        return str(created["id"])

    def _log(self, run: Run, workspace: str, title: str, **extra: str) -> Any:
        arguments = [
            "concept",
            "create",
            "activity_log",
            title,
            "--workspace",
            workspace,
            "--field",
            extra.pop("occurred_at", "occurred_at=2026-08-27T02:00:00Z"),
        ]
        if "folder" in extra:
            arguments += ["--in", extra["folder"]]
        _, made = run(*arguments)
        return made

    def test_the_year_folder_dates_the_filename(self, run: Run) -> None:
        made = self._log(
            run,
            self._workspace(run),
            "A session",
            folder="10_Workspaces/Example/Logs/2026",
        )
        assert made["path"] == "10_Workspaces/Example/Logs/2026/2026-08-27_a-session.md"
        assert "keeps its date" in made["placement"]

    def test_naming_no_folder_at_all_dates_it_too(self, run: Run) -> None:
        made = self._log(run, self._workspace(run), "A session")
        assert made["path"] == "10_Workspaces/Example/Logs/2026/2026-08-27_a-session.md"

    def test_the_logs_root_is_the_year_roll_up_and_says_so(self, run: Run) -> None:
        # The placement tells a caller which of the two things they just did.
        # An undated filename in `Logs/` looks like a mistake until you are told
        # it is the roll-up.
        made = self._log(
            run,
            self._workspace(run),
            "2026_summary",
            folder="10_Workspaces/Example/Logs",
        )
        # `2026_summary`, underscore intact: core/01 section 13 spells it that
        # way and the underscore separates fields, so a slug must not hyphenate
        # it away.
        assert made["path"] == "10_Workspaces/Example/Logs/2026_summary.md"
        assert "year roll-up" in made["placement"]
        assert "Logs/<YYYY>" in made["placement"]

    def test_the_year_comes_from_when_it_happened_not_from_today(self, run: Run) -> None:
        # A handoff written in January about December belongs under December.
        made = self._log(
            run,
            self._workspace(run),
            "Last year",
            occurred_at="occurred_at=2025-12-31T18:00:00-06:00",
        )
        assert made["path"] == "10_Workspaces/Example/Logs/2025/2025-12-31_last-year.md"

    def test_a_type_that_is_not_a_log_is_unaffected(self, run: Run) -> None:
        workspace = self._workspace(run)
        _, made = run(
            "concept",
            "create",
            "plan",
            "Somewhere else",
            "--workspace",
            workspace,
            "--in",
            "10_Workspaces/Example/Plans",
        )
        assert made["path"] == "10_Workspaces/Example/Plans/somewhere-else.md"
        assert made["placement"] == "10_Workspaces/Example/Plans was named"
