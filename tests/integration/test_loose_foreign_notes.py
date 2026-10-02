"""Loose notes at the top of a pile are foreign material too.

A folder of notes often keeps its notes at the top level, not in
subfolders. `init` registers each top-level Markdown file it finds there, by
name, exactly as it registers a top-level directory (core/01 section 1), so
the note is indexed by path and `search` reaches it on the first run. The
names Never4gA reserves at the vault root are never registered.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_OK, main

PILE = {
    "Projects/auth.md": "# Auth\n\nTokens rotate weekly.\n",
    "loose.md": "# Loose\n\nThe quillwort drip kit.\n",
    "README.md": "# My notes\n\nZanzibar cedar for the beds.\n",
    "photo.png": "not markdown",
}

Run = Callable[..., Any]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for relative, text in PILE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = True) -> Any:
        flags = ["--json"] if as_json else []
        code = main(["--vault", str(vault), *flags, *arguments])
        captured = capsys.readouterr()
        assert code == EXIT_OK, captured.err or captured.out
        return json.loads(captured.out) if as_json else captured.out

    return invoke


@pytest.fixture
def indexed(run: Run) -> Run:
    run("init")
    run("index")
    return run


class TestInit:
    def test_it_registers_each_loose_note_beside_the_directories(self, run: Run) -> None:
        result = run("init")
        assert result["foreign_material"] == [{"directory": "Projects", "files": 1}]
        assert result["foreign_notes"] == ["README.md", "loose.md"]

    def test_the_manifest_records_both_in_one_registry(self, run: Run) -> None:
        run("init")
        assert run("status")["foreign_material"] == ["Projects", "README.md", "loose.md"]

    def test_the_summary_names_them(self, run: Run) -> None:
        summary = run("init", as_json=False)
        assert "2 loose notes left in place" in summary
        assert "loose.md" in summary

    def test_nothing_is_written_to_them(self, run: Run, vault: Path) -> None:
        run("init")
        for relative, text in PILE.items():
            assert (vault / relative).read_text(encoding="utf-8") == text

    def test_a_second_run_does_nothing(self, run: Run, vault: Path) -> None:
        run("init")
        manifest = (vault / "50_System/system.md").read_text(encoding="utf-8")
        again = run("init")
        assert again["updated"] == []
        assert (vault / "50_System/system.md").read_text(encoding="utf-8") == manifest

    def test_a_note_added_later_is_registered_by_the_next_run(self, run: Run, vault: Path) -> None:
        run("init")
        (vault / "later.md").write_text("# Later\n", encoding="utf-8")
        again = run("init")
        assert again["updated"] == ["50_System/system.md"]
        assert "later.md" in again["foreign_notes"]


class TestTheReservedNamesAreNeverRegistered:
    @pytest.mark.parametrize("name", ["index.md", "home.md", "log.md"])
    def test_a_reserved_root_name_is_not_foreign(self, vault: Path, run: Run, name: str) -> None:
        (vault / name).write_text("# Mine\n", encoding="utf-8")
        assert name not in run("init")["foreign_notes"]
        assert name not in run("status")["foreign_material"]


class TestSearch:
    def test_index_counts_them(self, run: Run) -> None:
        run("init")
        assert run("index")["foreign"]["indexed"] == 3

    def test_search_reaches_a_loose_note_by_path(self, indexed: Run) -> None:
        (result,) = indexed("search", "quillwort")["results"]
        assert result["path"] == "loose.md"
        assert result["id"] is None
        assert result["reason"] == "foreign_material"


class TestDoctor:
    def test_a_loose_note_is_untracked_and_the_vault_is_healthy(self, indexed: Run) -> None:
        report = indexed("doctor")
        assert report["healthy"] is True
        codes = {f["path"]: f["code"] for f in report["findings"] if f.get("path")}
        assert codes["loose.md"] == "untracked_document"

    def test_a_note_added_after_init_says_init_registers_it(
        self, indexed: Run, vault: Path
    ) -> None:
        (vault / "later.md").write_text("# Later\n", encoding="utf-8")
        (finding,) = [f for f in indexed("doctor")["findings"] if f.get("path") == "later.md"]
        assert finding["code"] == "untracked_document"
        assert "never4ga init" in finding["repair_hint"]


class TestAdopt:
    def test_adopting_moves_it_home_and_search_finds_the_concept(
        self, indexed: Run, vault: Path
    ) -> None:
        adopted = indexed("adopt", "loose.md", "--type", "knowledge")
        assert adopted["path"] == "30_Knowledge/Notes/loose.md"
        assert not (vault / "loose.md").exists()
        indexed("index")
        (result,) = indexed("search", "quillwort")["results"]
        assert result["path"] == "30_Knowledge/Notes/loose.md"
        assert result["id"] is not None
