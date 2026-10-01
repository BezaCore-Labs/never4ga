"""Unprocessed Inbox items reach the startup pack as a signal.

`never4ga capture` writes a thought into `00_Inbox/` without deciding what it
is; deciding is the act of processing, and `core/01` section 5 keeps the Inbox
temporary. Without this signal, captures would age in a folder no startup
mentions.

A signal, not an item: an inbox file is not a concept, so it cannot ride the
metadata lanes. Local operational facts, like tracker state, arrive as signals
so the pack's shape does not change with them. Filing the items stays a
judgment call for whoever reads the pack; the provider only makes the pile
visible, with no classification (core/07 section 1).
"""

from __future__ import annotations

import pytest

from never4ga.adapters.fakes import InMemoryVaultFileStore
from never4ga.context.budget import startup_budget
from never4ga.context.inbox_signals import INBOX_LISTED_LIMIT, InboxSignalProvider
from never4ga.domain.context import ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, AcquisitionStage, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest


def request(depth: ContextDepth = ContextDepth.STARTUP) -> ContextRequest:
    return ContextRequest(scope=ScopeRequest(), depth=depth, budget=startup_budget())


def scope() -> ResolvedScope:
    return ResolvedScope(
        workspace_id=ConceptId.new(),
        workspace_path=VaultPath.parse("10_Workspaces/Demo"),
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
    )


@pytest.fixture
def files() -> InMemoryVaultFileStore:
    return InMemoryVaultFileStore()


def capture(files: InMemoryVaultFileStore, name: str) -> None:
    files.write_text(VaultPath.parse(f"00_Inbox/{name}"), "# a thought\n")


class TestTheSignal:
    def test_pending_items_are_counted_and_listed_oldest_first(
        self, files: InMemoryVaultFileStore
    ) -> None:
        capture(files, "2026-08-30_an-older-thought.md")
        capture(files, "2026-09-01_a-newer-thought.md")
        signals = InboxSignalProvider(files).collect(request(), scope())
        by_kind = {signal.kind: signal for signal in signals}
        assert by_kind["inbox.pending"].value == 2
        assert by_kind["inbox.items"].value == [
            "00_Inbox/2026-08-30_an-older-thought.md",
            "00_Inbox/2026-09-01_a-newer-thought.md",
        ]

    def test_an_empty_inbox_is_not_news(self, files: InMemoryVaultFileStore) -> None:
        assert InboxSignalProvider(files).collect(request(), scope()) == ()

    def test_reserved_navigation_is_not_an_item(self, files: InMemoryVaultFileStore) -> None:
        # `index.md` and `log.md` are OKF reserved files, part of the Inbox's
        # furniture rather than its contents (core/01).
        files.write_text(VaultPath.parse("00_Inbox/index.md"), "# Inbox\n")
        files.write_text(VaultPath.parse("00_Inbox/log.md"), "history\n")
        assert InboxSignalProvider(files).collect(request(), scope()) == ()

    def test_files_outside_the_inbox_are_not_items(self, files: InMemoryVaultFileStore) -> None:
        files.write_text(VaultPath.parse("30_Knowledge/Notes/thing.md"), "# note\n")
        assert InboxSignalProvider(files).collect(request(), scope()) == ()

    def test_the_listing_is_capped_and_the_count_is_not(
        self, files: InMemoryVaultFileStore
    ) -> None:
        # The pack is bounded (core/07); the count says how much work there
        # is, the listing says where to start.
        for index in range(INBOX_LISTED_LIMIT + 5):
            capture(files, f"2026-08-{index:02d}_thought-{index}.md")
        signals = InboxSignalProvider(files).collect(request(), scope())
        by_kind = {signal.kind: signal for signal in signals}
        assert by_kind["inbox.pending"].value == INBOX_LISTED_LIMIT + 5
        items = by_kind["inbox.items"].value
        assert isinstance(items, list)
        assert len(items) == INBOX_LISTED_LIMIT

    def test_the_reason_names_the_structure_stage(self, files: InMemoryVaultFileStore) -> None:
        capture(files, "2026-08-30_a-thought.md")
        signals = InboxSignalProvider(files).collect(request(), scope())
        assert all(signal.reason.stage is AcquisitionStage.STRUCTURE for signal in signals)


class TestWhenItRuns:
    def test_startup_only(self, files: InMemoryVaultFileStore) -> None:
        # core/04 section 34: startup once per session, focused many times
        # within it. The pile has not changed since the session opened.
        provider = InboxSignalProvider(files)
        assert provider.supports(request(ContextDepth.STARTUP))
        assert not provider.supports(request(ContextDepth.FOCUSED))
        assert not provider.supports(request(ContextDepth.DEEP))


class TestTheScratchpadReachesTheSession:
    """The scratchpad is as visible to a session as the Inbox pile.

    A durable file is quicker to write to than one capture per thought. It is
    only safe if a session is shown it; otherwise it looks like it remembers
    for you and does not.
    """

    def _scratchpad(self, files: InMemoryVaultFileStore, *thoughts: str) -> None:
        bullets = "".join(f"- {thought}\n" for thought in thoughts)
        files.write_text(
            VaultPath.parse("00_Inbox/scratchpad.md"),
            f"# Scratchpad\n\nThoughts too small to be a note.\n\n{bullets}",
        )

    def test_the_lines_arrive_counted_and_listed(self, files: InMemoryVaultFileStore) -> None:
        self._scratchpad(files, "compare two note apps", "water the plants")
        signals = {s.kind: s.value for s in InboxSignalProvider(files).collect(request(), scope())}
        assert signals["inbox.scratch"] == 2
        assert signals["inbox.scratch_items"] == ["compare two note apps", "water the plants"]

    def test_an_empty_scratchpad_is_not_news(self, files: InMemoryVaultFileStore) -> None:
        # The scaffolded file has prose and no bullets, and every vault has one
        # from `init`. If its mere existence were a signal, every pack would
        # carry it forever and it would stop meaning anything.
        self._scratchpad(files)
        assert InboxSignalProvider(files).collect(request(), scope()) == ()

    def test_the_scratchpad_is_never_a_pending_item(self, files: InMemoryVaultFileStore) -> None:
        self._scratchpad(files, "a thought")
        signals = {s.kind: s.value for s in InboxSignalProvider(files).collect(request(), scope())}
        assert "inbox.pending" not in signals

    def test_a_long_scratchpad_is_bounded_but_counted_whole(
        self, files: InMemoryVaultFileStore
    ) -> None:
        self._scratchpad(files, *(f"thought {n}" for n in range(30)))
        provider = InboxSignalProvider(files, scratch_limit=5)
        signals = {s.kind: s.value for s in provider.collect(request(), scope())}
        assert signals["inbox.scratch"] == 30
        listed = signals["inbox.scratch_items"]
        assert isinstance(listed, list)
        assert len(listed) == 5
