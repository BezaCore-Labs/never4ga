"""Context upkeep end to end: startup records, checkpoint declares, wrap asks.

The rule is core/04 section 37 ("Workspace state changes").

The unit of evidence is a real vault on disk driven through the CLI and the MCP
toolbox, because the three halves live in three verbs and the point is that
they meet: `context startup` records what its pack carried, `checkpoint
--context` records the claim, and `wrap` compares them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_FAILED, EXIT_OK, main
from never4ga.mcp.toolbox import Toolbox

Run = Callable[..., tuple[int, Any]]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "client-repo"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = True) -> tuple[int, Any]:
        flags = ["--json"] if as_json else []
        code = main(["--vault", str(vault), *flags, *arguments])
        out = capsys.readouterr().out
        return code, json.loads(out) if as_json else out

    return invoke


class Workspace:
    def __init__(self, state: dict[str, Any], history: dict[str, Any]) -> None:
        self.state = state
        self.history = history


@pytest.fixture
def workspace(run: Run, vault: Path, repository: Path) -> Workspace:
    run("init")
    _, created = run("workspace", "create", "Client Workspace", "--type", "product")
    run("workspace", "map", created["id"], "--repo", str(repository))
    _, state = run("concept", "create", "context", "Project State", "--workspace", created["id"])
    _, history = run("concept", "create", "context", "History", "--workspace", created["id"])
    # Far larger than a startup pack's character budget, so the pack can only
    # carry it as a reference. A wrap must still ask about a document it
    # carried that way.
    with (vault / history["path"]).open("a", encoding="utf-8") as file:
        file.write("\n" + "A paragraph of accumulated history.\n" * 2_000)
    run("index")
    return Workspace(state, history)


def start(run: Run, repository: Path) -> str:
    _, pack = run("context", "startup", "--path", str(repository))
    return str(pack["session_id"])


class TestStartupRecordsWhatItCarried:
    def test_a_document_carried_as_a_reference_is_given_too(
        self, run: Run, repository: Path, workspace: Workspace
    ) -> None:
        _, pack = run("context", "startup", "--path", str(repository))
        carried = {item["title"]: item["is_reference"] for item in pack["items"]}
        assert carried["History"] is True

        session = str(pack["session_id"])
        run("checkpoint", "looked around", "--session", session)
        _, wrapped = run("wrap", "--session", session)

        titles = [given["title"] for given in wrapped["context_given"]]
        assert titles == ["History", "Project State"]
        assert wrapped["context_question"] == "Did this session change anything these assert?"


class TestWrapHoldsTheSessionToIt:
    def test_an_unchanged_declared_document_fails_the_wrap(
        self, run: Run, repository: Path, workspace: Workspace
    ) -> None:
        session = start(run, repository)
        run(
            "checkpoint",
            "closed the milestone",
            "--session",
            session,
            "--context",
            f"{workspace.state['id']}: the milestone closed",
        )

        code, wrapped = run("wrap", "--session", session)

        assert code == EXIT_FAILED
        assert wrapped["outstanding_context"] == [workspace.state["path"]]
        assert wrapped["context"] == [f"{workspace.state['id']}: the milestone closed"]

    def test_correcting_it_clears_the_wrap(
        self, run: Run, vault: Path, repository: Path, workspace: Workspace
    ) -> None:
        session = start(run, repository)
        with (vault / workspace.state["path"]).open("a", encoding="utf-8") as file:
            file.write("\nThe milestone closed.\n")
        run(
            "checkpoint",
            "closed the milestone",
            "--session",
            session,
            "--context",
            workspace.state["path"],
        )

        code, wrapped = run("wrap", "--session", session)

        assert code == EXIT_OK
        assert wrapped["outstanding_context"] == []

    def test_it_can_be_let_through_deliberately(
        self, run: Run, repository: Path, workspace: Workspace
    ) -> None:
        session = start(run, repository)
        run("checkpoint", "x", "--session", session, "--context", workspace.state["id"])

        code, _ = run("wrap", "--session", session, "--allow-open-context")

        assert code == EXIT_OK

    def test_the_human_output_asks_and_names_the_gap(
        self, run: Run, repository: Path, workspace: Workspace
    ) -> None:
        session = start(run, repository)
        run("checkpoint", "x", "--session", session, "--context", workspace.state["id"])

        _, out = run("wrap", "--session", session, as_json=False)

        assert "Did this session change anything these assert?" in out
        assert f"  - Project State  ({workspace.state['path']})" in out
        assert "1 declared context document(s) did not change:" in out
        assert f"  - {workspace.state['path']}" in out


class TestTheToolboxDoesTheSame:
    def test_mcp_checkpoint_and_wrap_hold_the_same_account(
        self, vault: Path, repository: Path, workspace: Workspace
    ) -> None:
        toolbox = Toolbox(vault, local=True)
        pack = toolbox.context_startup({"cwd": str(repository)})
        session = str(pack["session_id"])
        recorded = toolbox.checkpoint(
            {"session_id": session, "note": "x", "context": [workspace.state["id"]]}
        )
        assert recorded["context"] == [workspace.state["id"]]

        wrapped = toolbox.wrap({"session_id": session})

        assert wrapped["outstanding_context"] == [workspace.state["path"]]
        assert [given["title"] for given in wrapped["context_given"]] == [
            "History",
            "Project State",
        ]
