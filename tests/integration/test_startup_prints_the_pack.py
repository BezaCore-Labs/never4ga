"""The human form of a pack carries what the pack carries.

The startup Skill tells an agent to read each item's body and to fetch a
reference by its id. A summary of titles alone would force a second
`context startup --json` run to read the pack, and every startup opens a
session (`core/04` section 34), so each such run leaves an empty session behind.

So the human form is the whole pack: the index first, then every body under
its path, with each reference marked and given the id to fetch it by.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from never4ga.cli import EXIT_OK, main

TITLE = "Where Things Stand"
MARKER = "The decisive sentence a session must be able to read."

Run = Callable[..., tuple[int, str]]


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
    def invoke(*arguments: str, as_json: bool = False) -> tuple[int, str]:
        flags = ["--json"] if as_json else []
        code = main(["--vault", str(vault), *flags, *arguments])
        return code, capsys.readouterr().out

    return invoke


@pytest.fixture
def context_path(run: Run, vault: Path, repository: Path) -> Path:
    run("init")
    _, out = run("workspace", "create", "Client Workspace", "--type", "product", as_json=True)
    workspace = json.loads(out)["id"]
    run("workspace", "map", workspace, "--repo", str(repository))
    code, out = run("concept", "create", "context", TITLE, "--workspace", workspace, as_json=True)
    assert code == EXIT_OK, out
    path: Path = vault / str(json.loads(out)["path"])
    path.write_text(path.read_text(encoding="utf-8") + f"\n{MARKER}\n", encoding="utf-8")
    run("index")
    return path


DECISION = "A Settled Thing"
DECISION_MARKER = "The reasoning a decision carries."


@pytest.fixture
def decision(run: Run, vault: Path, context_path: Path) -> str:
    (workspace,) = re.findall(r"^workspace: (\S+)$", context_path.read_text(), re.MULTILINE)
    code, out = run(
        "concept", "create", "decision", DECISION, "--workspace", workspace, as_json=True
    )
    assert code == EXIT_OK, out
    path = vault / str(json.loads(out)["path"])
    path.write_text(path.read_text(encoding="utf-8") + f"\n{DECISION_MARKER}\n", encoding="utf-8")
    run("index")
    return DECISION


def startup(run: Run, repository: Path, *extra: str, as_json: bool = False) -> str:
    code, out = run("context", "startup", "--path", str(repository), *extra, as_json=as_json)
    assert code == EXIT_OK, out
    return out


class TestOneRunIsEnough:
    def test_a_carried_body_is_printed(
        self, run: Run, repository: Path, context_path: Path
    ) -> None:
        assert MARKER in startup(run, repository)

    def test_each_body_is_headed_by_its_path(
        self, run: Run, vault: Path, repository: Path, context_path: Path
    ) -> None:
        out = startup(run, repository)
        relative = context_path.relative_to(vault).as_posix()
        heading = next(line for line in out.splitlines() if relative in line)
        assert out.index(heading) < out.index(MARKER)

    def test_the_index_still_comes_first(
        self, run: Run, repository: Path, context_path: Path
    ) -> None:
        out = startup(run, repository)
        assert out.index("session:") < out.index(MARKER)

    def test_a_reference_is_marked_with_the_id_to_fetch_it_by(
        self, run: Run, repository: Path, decision: str
    ) -> None:
        # A budget too small for any body turns every optional item into a
        # reference. A decision is optional; context is required reading
        # (core/07 section 10) and is marked with its path instead.
        pack = json.loads(startup(run, repository, "--max-characters", "1", as_json=True))
        (item,) = [item for item in pack["items"] if item["title"] == DECISION]
        assert item["is_reference"] is True

        out = startup(run, repository, "--max-characters", "1")
        (line,) = [line for line in out.splitlines() if DECISION in line and "[" in line]
        assert "reference" in line
        assert item["id"] in line
        assert DECISION_MARKER not in out

    def test_a_reference_says_why_it_is_one(
        self, run: Run, repository: Path, decision: str
    ) -> None:
        # core/07 section 10: a body larger than the whole budget is a document
        # a person must fix, and the pack says so rather than looking merely
        # full.
        pack = json.loads(startup(run, repository, "--max-characters", "1", as_json=True))
        (item,) = [item for item in pack["items"] if item["title"] == DECISION]
        assert item["reference_reason"] == "larger_than_budget"

        out = startup(run, repository, "--max-characters", "1")
        (line,) = [line for line in out.splitlines() if DECISION in line and "[" in line]
        assert "larger than the budget" in line

    def test_required_reading_that_does_not_fit_is_marked_with_its_path(
        self, run: Run, vault: Path, repository: Path, context_path: Path
    ) -> None:
        out = startup(run, repository, "--max-characters", "1")
        (line,) = [line for line in out.splitlines() if line.startswith("  context ")]
        assert f"read in full: {context_path}" in line
        assert MARKER not in out

    def test_a_carried_body_has_no_reference_reason(
        self, run: Run, repository: Path, context_path: Path
    ) -> None:
        pack = json.loads(startup(run, repository, as_json=True))
        (item,) = [item for item in pack["items"] if item["title"] == TITLE]
        assert item["reference_reason"] is None

    def test_a_focused_pack_prints_its_bodies_too(
        self, run: Run, repository: Path, context_path: Path
    ) -> None:
        code, out = run("context", "focus", "decisive", "sentence", "--path", str(repository))
        assert code == EXIT_OK, out
        assert MARKER in out
