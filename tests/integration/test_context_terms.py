"""Context terms reach the index parsed, through every interface.

Several words in one term must find what the same words as separate terms find,
not be searched for as one quoted phrase nobody wrote. These tests run against
a vault on disk and a real SQLite FTS5 index, where that quoting happens.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_OK, main
from never4ga.mcp.toolbox import Toolbox


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
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "never4ga"
    (root / ".git").mkdir(parents=True)
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
def prepared(run: Run, repository: Path) -> str:
    """A mapped, indexed workspace holding one research note titled Palette.

    A research note rather than a standard: deep context admits standards
    structurally whatever the terms say, and a document that arrives either way
    cannot show whether its words were searched for.
    """
    run("init")
    workspace = run("workspace", "create", "Never4gA", "--type", "product", as_json=True)
    workspace_id = str(workspace.json["id"])
    created = run(
        "concept",
        "create",
        "research_note",
        "Palette",
        "--workspace",
        workspace_id,
        "--in",
        "10_Workspaces/Never4gA/Research",
        as_json=True,
    )
    assert created.code == EXIT_OK, created.err
    run("workspace", "map", workspace_id, "--repo", str(repository))
    run("index")
    return workspace_id


def _titles(payload: Any) -> list[str]:
    return [item["title"] for item in payload["items"]]


class TestTheCli:
    def test_one_quoted_argument_finds_what_its_words_find(
        self, run: Run, prepared: str, repository: Path
    ) -> None:
        cwd = ("--cwd", str(repository))
        separate = run("context", "focus", "palette", "ownership", *cwd, as_json=True)
        together = run("context", "focus", "palette ownership", *cwd, as_json=True)
        assert "Palette" in _titles(separate.json)
        assert _titles(together.json) == _titles(separate.json)

    def test_deep_context_parses_them_too(self, run: Run, prepared: str, repository: Path) -> None:
        cwd = ("--cwd", str(repository), "--depth", "deep")
        separate = run("context", "focus", "palette", "ownership", *cwd, as_json=True)
        together = run("context", "focus", "palette ownership", *cwd, as_json=True)
        assert "Palette" in _titles(separate.json)
        assert _titles(together.json) == _titles(separate.json)

    def test_a_focused_pack_that_matched_nothing_says_so(
        self, run: Run, prepared: str, repository: Path
    ) -> None:
        # An empty pack must say its terms matched nothing, so it cannot be
        # read as "the vault has nothing about this" when the terms were
        # never searched for.
        result = run("context", "focus", "zzqx", "--cwd", str(repository))
        assert result.code == EXIT_OK
        assert "nothing in scope matched" in result.out

    def test_a_focused_pack_that_matched_something_does_not(
        self, run: Run, prepared: str, repository: Path
    ) -> None:
        result = run("context", "focus", "palette", "--cwd", str(repository))
        assert "nothing in scope matched" not in result.out


class TestTheMcpServer:
    def test_a_sentence_in_one_term_is_searched_for_its_words(
        self, run: Run, prepared: str, repository: Path, vault: Path
    ) -> None:
        # Agents are the callers most likely to put a phrase in one element.
        toolbox = Toolbox(vault, local=True)
        pack = toolbox.context_focus({"terms": ["palette ownership"], "cwd": str(repository)})
        assert "Palette" in _titles(pack)
