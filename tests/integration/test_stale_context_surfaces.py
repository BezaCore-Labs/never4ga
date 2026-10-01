"""A context document past its `stale_after` is flagged on every surface.

core/02 section 15.2 asks for stale content to be visibly flagged. The flag is
set once, during assembly; these hold that each interface carries it rather
than dropping a field it did not know about -- the CLI's JSON and its human
summary, the MCP toolbox, and the HTTP API's item model.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from never4ga.api.models import ContextItemModel
from never4ga.cli import EXIT_OK, main
from never4ga.domain.context import ContextItem, PackCategory, ReferenceReason
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.mcp.toolbox import Toolbox

LAPSED = "2020-01-01T00:00:00Z"
TITLE = "Where Things Stand"

Run = Callable[..., Any]


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
    def invoke(*arguments: str, as_json: bool = True) -> tuple[int, str]:
        flags = ["--json"] if as_json else []
        code = main(["--vault", str(vault), *flags, *arguments])
        return code, capsys.readouterr().out

    return invoke


@pytest.fixture
def mapped(run: Run, repository: Path) -> None:
    run("init")
    _, out = run("workspace", "create", "Client Workspace", "--type", "product")
    workspace = json.loads(out)["id"]
    run("workspace", "map", workspace, "--repo", str(repository))
    code, out = run(
        "concept",
        "create",
        "context",
        TITLE,
        "--workspace",
        workspace,
        "--field",
        f"stale_after={LAPSED}",
    )
    assert code == EXIT_OK, out
    run("index")


def the_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    (item,) = [item for item in items if item["title"] == TITLE]
    return item


class TestEverySurfaceFlagsIt:
    def test_the_cli_json_carries_the_instant(
        self, run: Run, repository: Path, mapped: None
    ) -> None:
        _, out = run("context", "startup", "--path", str(repository))
        item = the_item(json.loads(out)["items"])
        assert item["is_stale"] is True
        assert item["stale_since"] == "2020-01-01T00:00:00Z"

    def test_a_fresh_item_says_so(self, run: Run, repository: Path, mapped: None) -> None:
        _, out = run("context", "startup", "--path", str(repository))
        (workspace,) = [
            item for item in json.loads(out)["items"] if item["category"] == "workspace"
        ]
        assert workspace["is_stale"] is False
        assert workspace["stale_since"] is None

    def test_the_human_summary_marks_it(self, run: Run, repository: Path, mapped: None) -> None:
        _, out = run("context", "startup", "--path", str(repository), as_json=False)
        # The index line, not the body printed below it, which repeats the title.
        (line,) = [line for line in out.splitlines() if line.startswith("  context ")]
        assert TITLE in line
        assert "stale since 2020-01-01" in line

    def test_the_toolbox_carries_it(self, vault: Path, repository: Path, mapped: None) -> None:
        pack = Toolbox(vault, local=True).context_startup({"cwd": str(repository)})
        item = the_item(list(pack["items"]))
        assert item["stale_since"] == "2020-01-01T00:00:00Z"

    def test_the_api_model_carries_a_reference_reason(self) -> None:
        # The reference reason (core/07 section 10) rides the same item model,
        # so it is held in the same place.
        model = ContextItemModel.of(
            ContextItem(
                concept_id=ConceptId.new(),
                path=VaultPath.parse("10_Workspaces/Client/Context/state.md"),
                title=TITLE,
                priority=0,
                reason=AcquisitionReason.of(ReasonCode.STRUCTURAL_LOCATION),
                category=PackCategory.CONTEXT,
                is_reference=True,
                reference_reason=ReferenceReason.BUDGET_SPENT,
            )
        )
        assert model.reference_reason == "budget_spent"

    def test_the_toolbox_carries_a_reference_reason(
        self, run: Run, vault: Path, repository: Path, mapped: None
    ) -> None:
        _, out = run("context", "startup", "--path", str(repository))
        document = vault / the_item(json.loads(out)["items"])["path"]
        with document.open("a", encoding="utf-8") as handle:
            handle.write("x" * 50_000)
        run("index")
        pack = Toolbox(vault, local=True).context_startup({"cwd": str(repository)})
        item = the_item(list(pack["items"]))
        assert item["reference_reason"] == "larger_than_budget"

    def test_the_api_model_carries_it(self) -> None:
        lapsed = datetime(2020, 1, 1, tzinfo=UTC)
        model = ContextItemModel.of(
            ContextItem(
                concept_id=ConceptId.new(),
                path=VaultPath.parse("10_Workspaces/Client/Context/state.md"),
                title=TITLE,
                priority=0,
                reason=AcquisitionReason.of(ReasonCode.STRUCTURAL_LOCATION),
                category=PackCategory.CONTEXT,
                stale_since=lapsed,
            )
        )
        assert model.is_stale is True
        assert model.stale_since == "2020-01-01T00:00:00Z"
