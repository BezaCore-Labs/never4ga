"""The filesystem watcher's deterministic parts.

details/data-indexing-maintenance.md section 15: filesystem events are *hints*,
not the only truth. They are debounced and coalesced, noisy locations are
ignored, and reconciliation, not the watcher, is the guarantee (section 16).

Everything tested here runs on a fake clock and synthetic events. A test that
waits for a real inotify event to arrive tests the kernel; what Never4gA owns
is which events it cares about and how it groups them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import (
    DirModifiedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
)

from never4ga.service.watcher import Coalescer, VaultEventHandler, is_watched


class TestWhatIsWatched:
    """Section 15: ignore noisy and runtime locations."""

    @pytest.mark.parametrize(
        "relative",
        [
            "30_Knowledge/Notes/hybrid-retrieval.md",
            "50_System/system.md",
            "10_Workspaces/Never4gA/workspace.md",
        ],
    )
    def test_a_concept_is_watched(self, tmp_path: Path, relative: str) -> None:
        assert is_watched(tmp_path / relative, root=tmp_path)

    @pytest.mark.parametrize(
        "relative",
        [
            ".git/index",
            ".git/objects/ab/cdef",
            ".obsidian/workspace.json",
            ".obsidian/plugins/some-plugin/data.json",
            ".trash/deleted.md",
            ".never4ga/cache/whatever",
        ],
    )
    def test_a_noisy_location_is_ignored(self, tmp_path: Path, relative: str) -> None:
        # `.git/` alone would fire on every commit, and Never4gA never commits
        # the vault anyway (core/05 section 17).
        assert not is_watched(tmp_path / relative, root=tmp_path)

    def test_a_non_markdown_file_is_ignored(self, tmp_path: Path) -> None:
        # Only Markdown is canonical (core/00 #1). An image dropped in the
        # vault changes nothing the index holds.
        assert not is_watched(tmp_path / "30_Knowledge/Notes/diagram.png", root=tmp_path)

    def test_an_editor_swap_file_is_ignored(self, tmp_path: Path) -> None:
        # Obsidian, vim and friends write alongside the file they are editing.
        # Reacting to each intermediate write means reindexing mid-save.
        for name in (".hybrid-retrieval.md.swp", "hybrid-retrieval.md~", ".#lock.md"):
            assert not is_watched(tmp_path / "30_Knowledge/Notes" / name, root=tmp_path)

    def test_a_path_outside_the_vault_is_ignored(self, tmp_path: Path) -> None:
        assert not is_watched(tmp_path.parent / "elsewhere.md", root=tmp_path)

    def test_the_check_does_not_require_the_file_to_exist(self, tmp_path: Path) -> None:
        # A delete event names a path that is already gone, and a delete is
        # exactly the event that must not be dropped.
        assert is_watched(tmp_path / "30_Knowledge/Notes/deleted.md", root=tmp_path)


class TestCoalescing:
    """One reconciliation per burst, not one per event."""

    def test_nothing_is_due_when_nothing_happened(self) -> None:
        assert not Coalescer(quiet_period=0.5).due(now=100.0)

    def test_a_single_event_becomes_due_after_the_quiet_period(self) -> None:
        coalescer = Coalescer(quiet_period=0.5)
        coalescer.record(Path("a.md"), now=100.0)
        assert not coalescer.due(now=100.2)
        assert coalescer.due(now=100.6)

    def test_a_burst_produces_one_batch(self, tmp_path: Path) -> None:
        coalescer = Coalescer(quiet_period=0.5)
        for index in range(20):
            coalescer.record(tmp_path / f"{index}.md", now=100.0 + index * 0.01)
        assert not coalescer.due(now=100.3)
        batch = coalescer.take(now=101.0)
        assert len(batch) == 20

    def test_a_continuing_burst_keeps_resetting_the_timer(self) -> None:
        # A long save, or a `git checkout` touching hundreds of files, should
        # produce one run when it settles rather than a run every half second.
        coalescer = Coalescer(quiet_period=0.5)
        for step in range(10):
            coalescer.record(Path("a.md"), now=100.0 + step * 0.4)
            assert not coalescer.due(now=100.0 + step * 0.4 + 0.1)
        assert coalescer.due(now=100.0 + 9 * 0.4 + 0.6)

    def test_the_same_path_twice_is_one_entry(self) -> None:
        coalescer = Coalescer(quiet_period=0.5)
        coalescer.record(Path("a.md"), now=100.0)
        coalescer.record(Path("a.md"), now=100.1)
        assert coalescer.take(now=101.0) == (Path("a.md"),)

    def test_taking_a_batch_empties_it(self) -> None:
        coalescer = Coalescer(quiet_period=0.5)
        coalescer.record(Path("a.md"), now=100.0)
        coalescer.take(now=101.0)
        assert not coalescer.due(now=102.0)
        assert coalescer.take(now=102.0) == ()

    def test_a_batch_is_ordered(self) -> None:
        # Deterministic output makes a log line reproducible.
        coalescer = Coalescer(quiet_period=0.5)
        for name in ("c.md", "a.md", "b.md"):
            coalescer.record(Path(name), now=100.0)
        assert coalescer.take(now=101.0) == (Path("a.md"), Path("b.md"), Path("c.md"))

    def test_it_reports_whether_anything_is_pending(self) -> None:
        coalescer = Coalescer(quiet_period=0.5)
        assert not coalescer.pending
        coalescer.record(Path("a.md"), now=100.0)
        assert coalescer.pending


class TestTheEventHandler:
    """Translating watchdog's events into paths worth reconciling."""

    @pytest.fixture
    def recorded(self, tmp_path: Path) -> tuple[Coalescer, VaultEventHandler]:
        coalescer = Coalescer(quiet_period=0.5)
        clock = iter(100.0 + step * 0.001 for step in range(10_000))
        handler = VaultEventHandler(
            root=tmp_path,
            coalescer=coalescer,
            clock=lambda: next(clock),
        )
        return coalescer, handler

    def test_a_modified_concept_is_recorded(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        coalescer, handler = recorded
        handler.on_any_event(FileModifiedEvent(str(tmp_path / "30_Knowledge/Notes/a.md")))
        assert coalescer.pending

    @pytest.mark.parametrize("event_type", [FileCreatedEvent, FileDeletedEvent])
    def test_creation_and_deletion_are_recorded(
        self,
        recorded: tuple[Coalescer, VaultEventHandler],
        tmp_path: Path,
        event_type: type[FileCreatedEvent],
    ) -> None:
        coalescer, handler = recorded
        handler.on_any_event(event_type(str(tmp_path / "30_Knowledge/Notes/a.md")))
        assert coalescer.pending

    def test_a_move_records_both_ends(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        # A rename is a delete and a create as far as the index is concerned,
        # and identity survives it because identity is in the frontmatter
        # (core/02 section 5.1).
        coalescer, handler = recorded
        handler.on_any_event(
            FileMovedEvent(
                str(tmp_path / "30_Knowledge/Notes/old.md"),
                str(tmp_path / "30_Knowledge/Notes/new.md"),
            )
        )
        assert len(coalescer.take(now=200.0)) == 2

    def test_a_git_event_is_ignored(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        coalescer, handler = recorded
        handler.on_any_event(FileModifiedEvent(str(tmp_path / ".git/index")))
        assert not coalescer.pending

    def test_an_obsidian_event_is_ignored(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        coalescer, handler = recorded
        handler.on_any_event(FileModifiedEvent(str(tmp_path / ".obsidian/workspace.json")))
        assert not coalescer.pending

    def test_a_directory_event_is_ignored(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        coalescer, handler = recorded
        handler.on_any_event(DirModifiedEvent(str(tmp_path / "30_Knowledge/Notes")))
        assert not coalescer.pending

    def test_merely_opening_a_file_is_not_a_change(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        coalescer, handler = recorded
        handler.on_any_event(FileOpenedEvent(str(tmp_path / "30_Knowledge/Notes/a.md")))
        assert not coalescer.pending

    def test_a_bytes_path_is_understood(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        # watchdog carries bytes paths on some platforms and for some events.
        coalescer, handler = recorded
        handler.on_any_event(FileModifiedEvent(str(tmp_path / "30_Knowledge/Notes/a.md").encode()))
        assert coalescer.pending

    def test_an_undecodable_path_is_dropped_rather_than_raising(
        self, recorded: tuple[Coalescer, VaultEventHandler], tmp_path: Path
    ) -> None:
        # A handler that raises kills the observer thread, and the watcher
        # stops without saying so (core/05 section 19).
        coalescer, handler = recorded
        handler.on_any_event(FileModifiedEvent(b"\xff\xfe/not/utf8.md"))
        assert not coalescer.pending
