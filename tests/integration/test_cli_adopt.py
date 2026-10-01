"""`never4ga adopt` end to end.

The service-level behaviour lives in `test_adoption.py`. This covers what the
verb adds: the path a person actually has in hand (vault-relative or the one
their shell completes), the flags, and the exit codes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import main


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
def ready(run: Run, vault: Path) -> Path:
    assert run("init").code == 0
    return vault


def hand_written(vault: Path, relative: str, text: str = "# By Hand\n\nprose\n") -> Path:
    absolute = vault / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(text, encoding="utf-8")
    return absolute


class TestTheVerb:
    def test_a_vault_relative_path_is_adopted(self, run: Run, ready: Path) -> None:
        hand_written(ready, "30_Knowledge/Notes/by-hand.md")

        result = run("adopt", "30_Knowledge/Notes/by-hand.md")
        assert result.code == 0
        assert "Adopted knowledge" in result.out
        assert "adopted where it sits" in result.out

    def test_a_filesystem_path_names_the_same_file(self, run: Run, ready: Path) -> None:
        absolute = hand_written(ready, "30_Knowledge/Notes/by-hand.md")

        result = run("adopt", str(absolute))
        assert result.code == 0
        assert "30_Knowledge/Notes/by-hand.md" in result.out

    def test_the_json_payload_carries_the_concept(self, run: Run, ready: Path) -> None:
        hand_written(ready, "30_Knowledge/Notes/by-hand.md")

        result = run("adopt", "30_Knowledge/Notes/by-hand.md", as_json=True)
        assert result.code == 0
        assert result.json["path"] == "30_Knowledge/Notes/by-hand.md"
        assert "adopted where it sits" in result.json["placement"]

    def test_type_and_fields_are_passed_through(self, run: Run, ready: Path) -> None:
        hand_written(ready, "30_Knowledge/Notes/by-hand.md")

        result = run("adopt", "30_Knowledge/Notes/by-hand.md", "--type", "knowledge")
        assert result.code == 0

    def test_a_title_field_names_the_adopted_concept(self, run: Run, ready: Path) -> None:
        # A heading plus --field title= must adopt under the supplied title,
        # not fail.
        absolute = hand_written(ready, "30_Knowledge/Notes/by-hand.md", "# Some title\n\nprose\n")

        result = run("adopt", "30_Knowledge/Notes/by-hand.md", "--field", "title=Another title")
        assert result.code == 0, result.err
        assert "Adopted knowledge Another title" in result.out
        assert "title: Another title" in absolute.read_text(encoding="utf-8")

    @pytest.mark.parametrize("field", ["id=garbage", "type=garbage", "schema=garbage"])
    def test_an_owned_field_is_a_refusal_not_a_traceback(
        self, run: Run, ready: Path, field: str
    ) -> None:
        # An owned field is refused with a structured error, never raised as
        # an exception or written to the file.
        absolute = hand_written(ready, "30_Knowledge/Notes/by-hand.md")
        before = absolute.read_text(encoding="utf-8")

        result = run("adopt", "30_Knowledge/Notes/by-hand.md", "--field", field, as_json=True)
        assert result.code != 0
        assert json.loads(result.err)["error"]["code"] == "concept_not_adopted"
        assert absolute.read_text(encoding="utf-8") == before

    def test_a_refusal_exits_nonzero_and_writes_nothing(self, run: Run, ready: Path) -> None:
        absolute = hand_written(ready, "30_Knowledge/stray.md")
        before = absolute.read_text(encoding="utf-8")

        result = run("adopt", "30_Knowledge/stray.md")
        assert result.code != 0
        assert absolute.read_text(encoding="utf-8") == before

    def test_a_refusal_carries_the_code_the_api_gives_it(self, run: Run, ready: Path) -> None:
        # `POST /v1/concepts/adopt` says `concept_not_adopted`, and the CLI
        # must say the same: one refusal, one payload shape on every surface.
        hand_written(ready, "10_Workspaces/Never4gA/Strategy/positioning.md")
        run("workspace", "create", "Never4gA", "--type", "product")

        result = run("adopt", "10_Workspaces/Never4gA/Strategy/positioning.md", as_json=True)
        assert result.code != 0
        error = json.loads(result.err)["error"]
        assert error["code"] == "concept_not_adopted"
        assert "--type" in error["repair_hint"]

    def test_concept_create_keeps_its_own_code(self, run: Run, ready: Path) -> None:
        # The other half of the handler: a creation refusal is still a creation.
        result = run("concept", "create", "decision", "Orphaned", as_json=True)
        assert result.code != 0
        assert json.loads(result.err)["error"]["code"] == "concept_not_created"

    def test_doctor_stops_reporting_what_was_adopted(self, run: Run, ready: Path) -> None:
        hand_written(ready, "30_Knowledge/Notes/by-hand.md")

        before = run("doctor", as_json=True)
        assert any(finding["code"] == "untracked_document" for finding in before.json["findings"])
        assert run("adopt", "30_Knowledge/Notes/by-hand.md").code == 0
        after = run("doctor", as_json=True)
        assert not any(
            finding["code"] == "untracked_document" for finding in after.json["findings"]
        )

    def test_search_finds_the_adopted_note(self, run: Run, ready: Path) -> None:
        hand_written(
            ready,
            "30_Knowledge/Notes/by-hand.md",
            "# By Hand\n\nxylophone maintenance schedule\n",
        )
        assert run("adopt", "30_Knowledge/Notes/by-hand.md").code == 0
        assert run("index").code == 0

        result = run("search", "xylophone", as_json=True)
        assert result.code == 0
        assert any("by-hand" in str(hit.get("path", "")) for hit in result.json["results"])
