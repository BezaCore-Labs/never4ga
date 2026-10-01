"""GitSignalProvider -- branch, head, dirtiness, changed paths, recent commits.

core/07 section 13: "Never4gA should prefer invoking the installed `git` CLI over
parsing `.git/` internals itself unless a compelling reason appears." So this
shells out, and reads nothing inside `.git/`.

Every value is a fact rather than a sentence. `domain/signals.py` enforces that
structurally -- single-line, short, scalar or a small structure of scalars -- so a
provider that wanted to summarise would be rejected by its own contract rather
than by review.

**Failure is not fatal.** A machine with no `git`, a repository in a broken
state, or a slow filesystem produces no signals; `StartupContextAssembler`
records the provider as degraded and returns the pack. core/05 section 19: a
failed optional subsystem must not destroy the service.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import PurePath
from typing import Final

from never4ga.domain.context import ContextRequest
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope
from never4ga.domain.signals import MAX_SIGNAL_TEXT_LENGTH, ContextSignal

__all__ = ["GitSignalProvider"]

#: Long enough for a real repository's working set, short enough that a pack
#: cannot be flooded by a branch nobody has committed yet.
MAX_CHANGED_PATHS: Final = 20

#: What "recently" means for commit subjects at startup.
MAX_RECENT_COMMITS: Final = 5

#: A mechanical read that has not answered in this long is not going to help a
#: bounded startup. core/07 section 15 requires startup to stay bounded, and an
#: unbounded subprocess is the easiest way to lose that.
TIMEOUT_SECONDS: Final = 5.0


class GitSignalProvider:
    """Read-only Git state for the repository a scope resolved to."""

    provider_id = "git"

    def __init__(self, executable: str = "git", timeout: float = TIMEOUT_SECONDS) -> None:
        self._executable = executable
        self._timeout = timeout

    def supports(self, request: ContextRequest) -> bool:
        """Every depth wants to know what is checked out."""
        return True

    def collect(self, request: ContextRequest, scope: ResolvedScope) -> Sequence[ContextSignal]:
        root = scope.repository_root
        if root is None:
            # A workspace need not have a repository. Nothing to say is not a
            # failure, and reporting it as one would mark the provider degraded
            # every time somebody worked in a Life area.
            return ()

        # `status` is the probe: it is the read that fails when this is not a
        # repository at all, which is the failure worth degrading the provider
        # for.
        status = self._run(root, "status", "--porcelain")

        # `branch --show-current` rather than `rev-parse --abbrev-ref HEAD`,
        # because a repository with no commits yet has a branch but no HEAD, and
        # a freshly initialised repository is a perfectly ordinary place to be
        # working. It reports nothing on a detached HEAD, where the commit is
        # the answer anyway.
        branch = self._optional(root, "branch", "--show-current")

        # Both need a commit to exist. On an empty repository they are absent
        # rather than fatal.
        head = self._optional(root, "rev-parse", "--short", "HEAD")
        log = self._optional(root, "log", f"-{MAX_RECENT_COMMITS}", "--pretty=format:%s")

        changed = _changed_paths(status)
        signals = [
            self._signal("git.branch", branch),
            self._signal("git.head", head),
            self._signal("git.dirty", bool(changed)),
            self._signal("git.changed_paths", changed[:MAX_CHANGED_PATHS]),
            self._signal("git.changed_count", len(changed)),
            self._signal("git.recent_commits", [_clip(line) for line in log.splitlines() if line]),
        ]
        return tuple(signal for signal in signals if signal is not None)

    # -- internals --------------------------------------------------------

    def _signal(self, kind: str, value: object) -> ContextSignal | None:
        if value == "" or value == []:
            return None
        return ContextSignal(
            provider_id=self.provider_id,
            kind=kind,
            value=value,  # type: ignore[arg-type]
            reason=AcquisitionReason.of(ReasonCode.GIT_CHANGED_FILE, detail=kind),
        )

    def _optional(self, root: PurePath, *arguments: str) -> str:
        """A read that may legitimately have no answer.

        A repository with no commits has no HEAD and no log. That is a state to
        report around, not a broken provider -- so this returns nothing and lets
        the rest of the signals through. A missing `git` binary still raises,
        because that is not a repository state at all.
        """
        try:
            return self._run(root, *arguments)
        except subprocess.CalledProcessError:
            return ""

    def _run(self, root: PurePath, *arguments: str) -> str:
        """One read. Raises so the assembler can degrade the whole provider."""
        # A fixed argv and no shell: nothing here interpolates user text into a
        # command line.
        completed = subprocess.run(
            [self._executable, "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=True,
        )
        # Only trailing newlines: `git status --porcelain` puts the status in
        # the first two columns, so stripping leading whitespace would eat the
        # first entry's status and take a character off its path with it.
        return completed.stdout.rstrip("\n")


def _changed_paths(status: str) -> list[str]:
    """Paths from `git status --porcelain`, whatever the change was.

    The porcelain format is stable by contract, which is why it is worth parsing
    and `git status` in its human form is not.
    """
    paths = []
    for line in status.splitlines():
        if len(line) <= 3:
            continue
        path = line[3:].strip()
        # A rename reads `old -> new`; the new name is the one that exists.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(_clip(path.strip('"')))
    return paths


def _clip(text: str) -> str:
    """Signals are short by contract; a long subject is truncated, not dropped."""
    if len(text) <= MAX_SIGNAL_TEXT_LENGTH:
        return text
    return text[: MAX_SIGNAL_TEXT_LENGTH - 1] + "…"
