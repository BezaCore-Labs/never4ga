"""What is waiting in the Inbox, put where a session starts.

`never4ga capture` writes a thought into `00_Inbox/` precisely so that nothing
has to be decided in the moment. An inbox item is not a concept, because
choosing a type *is* the act of processing it (`services/capture.py`). The
other half of that bargain is visibility: a pile nobody is shown is a pile
nobody processes.

So the pile arrives as a *signal*, the same shape as tracker state and
sessions: a local operational fact beside the pack, not an item in it. The
provider counts and lists; it never classifies. Filing each item (a knowledge
note, a work item, workspace material, or the bin) is a judgment the reader of
the pack makes with the verbs that already exist (`concept create`, `work
create`). Mechanical acquisition ends where classification begins (core/07).

The Inbox is vault-global, deliberately: a thought captured while working in
one workspace is every session's business until somebody files it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from never4ga.domain.context import ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope
from never4ga.domain.signals import ContextSignal
from never4ga.layout import (
    INBOX_RESERVED_NAMES,
    INBOX_SCRATCHPAD_NAME,
    VaultRoot,
)
from never4ga.ports.vault_files import VaultFileStore

__all__ = ["INBOX_LISTED_LIMIT", "SCRATCH_LISTED_LIMIT", "InboxSignalProvider"]

#: How many item paths ride in the signal. The count always tells the whole
#: truth; the listing is where to start, and a startup pack is bounded by
#: design (core/07). Capture's dated-first filenames make lexical order
#: oldest-first, so the listing begins with what has waited longest.
INBOX_LISTED_LIMIT: Final = 10

#: The same bound for the scratchpad's lines. A line is one sentence where a
#: capture is a paragraph, so more of them fit in the same budget.
SCRATCH_LISTED_LIMIT: Final = 20

#: A bullet on the scratchpad. Every top-level one is a thought; the scaffolded
#: prose deliberately has none, so nothing has to be fenced off.
_BULLET: Final = "- "


class InboxSignalProvider:
    """Unprocessed captures, counted and listed, never classified."""

    provider_id = "inbox"

    def __init__(
        self,
        files: VaultFileStore,
        *,
        limit: int = INBOX_LISTED_LIMIT,
        scratch_limit: int = SCRATCH_LISTED_LIMIT,
    ) -> None:
        self._files = files
        self._limit = limit
        self._scratch_limit = scratch_limit

    def supports(self, request: ContextRequest) -> bool:
        """Startup only.

        core/04 section 34: startup runs once per session, focused retrieval
        many times within it -- and the pile has not changed since the session
        opened.
        """
        return request.depth is ContextDepth.STARTUP

    def collect(self, request: ContextRequest, scope: ResolvedScope) -> Sequence[ContextSignal]:
        prefix = f"{VaultRoot.INBOX.value}/"
        pending = sorted(
            str(path)
            for path in self._files.iter_paths()
            if str(path).startswith(prefix) and path.name not in INBOX_RESERVED_NAMES
        )
        scratch = self._scratch_lines()
        if not pending and not scratch:
            # An empty inbox is the normal state, not news worth a signal.
            return ()
        reason = AcquisitionReason.of(ReasonCode.STRUCTURAL_LOCATION, detail=VaultRoot.INBOX.value)
        signals: list[ContextSignal] = []
        if pending:
            signals.extend(
                (
                    ContextSignal(
                        provider_id=self.provider_id,
                        kind="inbox.pending",
                        value=len(pending),
                        reason=reason,
                    ),
                    ContextSignal(
                        provider_id=self.provider_id,
                        kind="inbox.items",
                        value=list(pending[: self._limit]),
                        reason=reason,
                    ),
                )
            )
        if scratch:
            signals.extend(
                (
                    ContextSignal(
                        provider_id=self.provider_id,
                        kind="inbox.scratch",
                        value=len(scratch),
                        reason=reason,
                    ),
                    ContextSignal(
                        provider_id=self.provider_id,
                        kind="inbox.scratch_items",
                        value=list(scratch[: self._scratch_limit]),
                        reason=reason,
                    ),
                )
            )
        return tuple(signals)

    def _scratch_lines(self) -> list[str]:
        """The scratchpad's thoughts, in the order they were written.

        This is the one file the provider opens. The rule is that acquisition
        does not *classify*, not that it does not read: listing ten captures
        reads ten filenames, and listing ten scratchpad lines reads one file.
        The provider still never decides what any of them are.

        A scratchpad nobody is shown looks like it remembers for you and does
        not.
        """
        text = self._files.read_text(
            VaultPath.parse(f"{VaultRoot.INBOX.value}/{INBOX_SCRATCHPAD_NAME}")
        )
        if text is None:
            return []
        return [
            line.strip()[len(_BULLET) :].strip()
            for line in text.splitlines()
            if line.startswith(_BULLET) and line.strip() != _BULLET.strip()
        ]
