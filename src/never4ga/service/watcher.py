"""Filesystem events as hints (details/data-indexing-maintenance.md section 15).

```text
filesystem event -> debounce/coalesce -> index changed paths
```

Two things follow from treating events as hints.

**Reconciliation is the guarantee, not the watcher.** A missed inotify event, a
vault edited while the service was stopped, a queue overflow during a `git
checkout`: all of them are recovered by the reconciliation the service runs
at startup, periodically, and on demand (section 16). The watcher makes the
index timely; it is not what makes it correct.

**A burst is one change.** Saving a file produces several events, and a branch
switch produces thousands. Every one of them is coalesced into a single batch
that fires once the vault goes quiet.

Section 15 also names what to ignore: `.git/`, `.obsidian/`, and runtime or
cache paths. Watching `.git/` would wake the indexer on every commit, and
Never4gA never commits the vault itself (core/05 section 17).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

from watchdog.events import (
    EVENT_TYPE_CLOSED,
    EVENT_TYPE_CLOSED_NO_WRITE,
    EVENT_TYPE_OPENED,
    FileSystemEvent,
    FileSystemEventHandler,
)

__all__ = ["Coalescer", "VaultEventHandler", "is_watched"]

#: Directories whose contents are never a canonical change. `.git` and
#: `.obsidian` are named by section 15. The rest follow the same reasoning:
#: they hold another tool's state, not the vault's content.
IGNORED_DIRECTORIES: Final = frozenset(
    {
        ".git",
        ".obsidian",
        ".trash",
        ".never4ga",
        ".stfolder",
        "__pycache__",
    }
)

#: Only Markdown is canonical (core/00 #1). Everything else in the vault is
#: content the index does not hold.
_MARKDOWN: Final = ".md"

#: What editors leave beside the file they are saving. Reacting to these means
#: reindexing a document mid-write.
_TEMPORARY_PREFIXES: Final = (".#", "~")
_TEMPORARY_SUFFIXES: Final = (".swp", ".swx", "~", ".tmp")

#: Events that report access rather than change.
_UNINTERESTING: Final = frozenset(
    {EVENT_TYPE_OPENED, EVENT_TYPE_CLOSED, EVENT_TYPE_CLOSED_NO_WRITE}
)


def is_watched(path: Path, *, root: Path) -> bool:
    """Whether a change to ``path`` could change what the index holds.

    Deliberately does not touch the filesystem: a delete event names a path
    that is already gone, and a delete is the event that must not be dropped.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if any(part in IGNORED_DIRECTORIES for part in relative.parts):
        return False
    name = relative.name
    if name.startswith(_TEMPORARY_PREFIXES) or name.endswith(_TEMPORARY_SUFFIXES):
        return False
    if name.startswith(".") and not name.endswith(_MARKDOWN):
        return False
    return name.endswith(_MARKDOWN)


class Coalescer:
    """Collects changed paths and releases them once the vault goes quiet.

    The quiet period restarts with every event, so a long operation produces
    one batch when it settles rather than a batch every ``quiet_period``.

    Safe to call from the observer thread and the service thread at once: the
    observer records, the service asks.
    """

    def __init__(self, *, quiet_period: float) -> None:
        self._quiet_period = quiet_period
        self._paths: set[Path] = set()
        self._last_event: float | None = None
        self._lock = threading.Lock()

    @property
    def pending(self) -> bool:
        with self._lock:
            return bool(self._paths)

    def record(self, path: Path, *, now: float) -> None:
        with self._lock:
            self._paths.add(path)
            self._last_event = now

    def due(self, *, now: float) -> bool:
        with self._lock:
            return self._is_due(now)

    def take(self, *, now: float) -> tuple[Path, ...]:
        """The batch, if it is due, and nothing otherwise."""
        with self._lock:
            if not self._is_due(now):
                return ()
            batch = tuple(sorted(self._paths))
            self._paths.clear()
            self._last_event = None
            return batch

    def _is_due(self, now: float) -> bool:
        if not self._paths or self._last_event is None:
            return False
        return now - self._last_event >= self._quiet_period


class VaultEventHandler(FileSystemEventHandler):
    """Turns watchdog events into paths worth reconciling.

    Nothing here raises. An exception in this handler kills the observer
    thread, and the watcher would then stop without anything saying so, the
    silent failure core/05 section 19 forbids.
    """

    def __init__(
        self,
        *,
        root: Path,
        coalescer: Coalescer,
        clock: Callable[[], float],
    ) -> None:
        self._root = root
        self._coalescer = coalescer
        self._clock = clock

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type in _UNINTERESTING:
            return
        now = self._clock()
        for raw in (event.src_path, event.dest_path):
            path = _as_path(raw)
            if path is not None and is_watched(path, root=self._root):
                self._coalescer.record(path, now=now)


def _as_path(raw: str | bytes | None) -> Path | None:
    """watchdog carries ``bytes`` on some platforms; a path we cannot decode is
    a path we cannot index, and dropping it is better than raising."""
    if not raw:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode()
        except UnicodeDecodeError:
            return None
    return Path(raw)
