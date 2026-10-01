"""A pile, initialised and indexed through the CLI.

Real files, real SQLite, `main()` in-process. The scratch pile has notes with
and without frontmatter, nested folders, a `README.md` and a `.obsidian/`
directory. Indexing is tested here; search and the Context Pack carrying a
foreign note are in `test_foreign_material_surfaces.py`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.adapters.fakes import (
    InMemoryGraphIndex,
    InMemoryIndexState,
    InMemoryMetadataIndex,
    InMemoryTextIndex,
)
from never4ga.adapters.filesystem import FileSystemMarkdownStore
from never4ga.cli import EXIT_FAILED, EXIT_OK, main
from never4ga.domain.document import ForeignNote, VaultPath
from never4ga.services.indexing import IndexService

Run = Callable[..., "Result"]


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out)


def write(vault: Path, relative: str, text: str) -> Path:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def seed_pile(vault: Path) -> None:
    write(vault, "Projects/garden/raised-beds.md", "# Raised beds\n\nCedar, not pine.\n")
    write(vault, "Projects/README.md", "# Projects\n\nWhat I am building.\n")
    write(
        vault,
        "Reading/fast-and-slow.md",
        "---\ntags: [books]\nrating: 4\n---\n# Fast and slow\n\nSystem one is quick.\n",
    )
    write(vault, "Reading/.obsidian/workspace.md", "tool state, not a note\n")
    write(vault, ".obsidian/app.json", "{}")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    seed_pile(tmp_path)
    return tmp_path


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        argv = ["--vault", str(vault), *(["--json"] if as_json else []), *arguments]
        code = main(argv)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def initialized(run: Run) -> Run:
    assert run("init").code == EXIT_OK
    return run


class TestTheFirstIndex:
    def test_index_reports_the_foreign_notes_it_indexed(self, initialized: Run) -> None:
        payload = initialized("index", as_json=True).json
        assert payload["foreign"] == {"indexed": 3, "unchanged": 0, "removed": 0}

    def test_the_human_report_says_so_too(self, initialized: Run) -> None:
        out = initialized("index").out
        assert "foreign notes: 3 indexed, 0 unchanged, 0 removed" in out

    def test_the_index_is_current_afterwards(self, initialized: Run) -> None:
        initialized("index")
        status = initialized("index", "--status", as_json=True)
        assert status.code == EXIT_OK, status.out
        assert status.json["stale"] is False
        assert status.json["indexed_foreign"] == 3

    def test_a_note_added_later_is_new_until_indexed(self, initialized: Run, vault: Path) -> None:
        initialized("index")
        write(vault, "Reading/new-book.md", "# A new book\n")
        status = initialized("index", "--status", as_json=True)
        assert status.code == EXIT_FAILED
        assert status.json["new"] == ["Reading/new-book.md"]

    def test_nothing_in_the_pile_was_written(self, initialized: Run, vault: Path) -> None:
        before = {path: path.read_bytes() for path in (vault / "Projects").rglob("*.md")} | {
            path: path.read_bytes() for path in (vault / "Reading").rglob("*.md")
        }
        initialized("index")
        for path, content in before.items():
            assert path.read_bytes() == content

    def test_doctor_stays_free_of_errors(self, initialized: Run) -> None:
        initialized("index")
        payload = initialized("doctor", as_json=True).json
        assert [f for f in payload["findings"] if f["severity"] == "error"] == []
        assert "index_is_stale" not in {f["code"] for f in payload["findings"]}


class TestReconciling:
    def test_a_rename_leaves_one_record_at_the_new_path(
        self, initialized: Run, vault: Path
    ) -> None:
        initialized("index")
        os.rename(vault / "Projects/garden/raised-beds.md", vault / "Projects/garden/beds.md")
        # A full pass reads and reindexes everything, foreign notes as
        # concepts; `--changed` is the pass that counts what it skipped.
        payload = initialized("index", "--changed", as_json=True).json
        assert payload["foreign"] == {"indexed": 1, "unchanged": 2, "removed": 1}
        status = initialized("index", "--status", as_json=True).json
        assert status["stale"] is False
        assert status["indexed_foreign"] == 3

    def test_a_deletion_removes_the_record(self, initialized: Run, vault: Path) -> None:
        initialized("index")
        (vault / "Reading/fast-and-slow.md").unlink()
        payload = initialized("index", as_json=True).json
        assert payload["foreign"]["removed"] == 1
        assert initialized("index", "--status", as_json=True).json["indexed_foreign"] == 2

    def test_a_rebuild_recovers_them(self, initialized: Run) -> None:
        initialized("index")
        payload = initialized("rebuild", as_json=True).json
        assert payload["foreign"]["indexed"] == 3


class CountingStore(FileSystemMarkdownStore):
    """The real store, counting every foreign note it reads."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.foreign_reads = 0

    def get_foreign_note(self, path: VaultPath) -> ForeignNote | None:
        self.foreign_reads += 1
        return super().get_foreign_note(path)


class TestTheSettledPass:
    """The watcher's pass trusts the stat, for foreign notes as for concepts."""

    @pytest.fixture
    def service(self, initialized: Run, vault: Path) -> tuple[IndexService, CountingStore]:
        store = CountingStore(vault)
        service = IndexService(
            documents=store,
            metadata=InMemoryMetadataIndex(),
            text=InMemoryTextIndex(),
            graph=InMemoryGraphIndex(),
            state=InMemoryIndexState(),
        )
        service.reconcile()
        return service, store

    def test_an_unchanged_pile_is_not_read(
        self, service: tuple[IndexService, CountingStore]
    ) -> None:
        indexer, store = service
        before = store.foreign_reads
        run = indexer.reconcile(changed_only=True)
        assert store.foreign_reads == before
        assert (run.foreign_indexed, run.foreign_unchanged) == (0, 3)

    def test_an_edited_note_is_the_only_one_read(
        self, service: tuple[IndexService, CountingStore], vault: Path
    ) -> None:
        indexer, store = service
        write(vault, "Reading/fast-and-slow.md", "# Fast and slow\n\nSystem two is slow.\n")
        before = store.foreign_reads
        run = indexer.reconcile(changed_only=True)
        assert store.foreign_reads - before == 1
        assert (run.foreign_indexed, run.foreign_unchanged) == (1, 2)
