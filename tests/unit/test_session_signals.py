"""What one client's session tells another.

A wrapped session already reaches a pack as its `activity_log`; this is about
the ones that have not been wrapped yet, which are otherwise invisible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from never4ga.adapters.fakes.session_store import InMemorySessionStore
from never4ga.context.budget import startup_budget
from never4ga.context.session_signals import SessionSignalProvider
from never4ga.domain.context import ContextDepth, ContextRequest
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest
from never4ga.domain.sessions import Checkpoint, Session

WORKSPACE = ConceptId.new()
START = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def provider(store: InMemorySessionStore) -> SessionSignalProvider:
    return SessionSignalProvider(store)


@pytest.fixture
def scope() -> ResolvedScope:
    return ResolvedScope(
        workspace_id=WORKSPACE,
        workspace_path=VaultPath.parse("10_Workspaces/Demo"),
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
    )


def _request(depth: ContextDepth = ContextDepth.STARTUP) -> ContextRequest:
    return ContextRequest(
        scope=ScopeRequest(workspace_id=WORKSPACE),
        depth=depth,
        budget=startup_budget(),
    )


def _session(
    store: InMemorySessionStore,
    *,
    client: str = "claude-code",
    minutes: int = 0,
    notes: tuple[str, ...] = ("did a thing",),
) -> Session:
    opened = Session.opened(
        workspace=WORKSPACE,
        actor=f"{client}/model",
        started_at=START + timedelta(minutes=minutes),
        client=client,
    )
    store.open(opened)
    for index, note in enumerate(notes):
        store.append(
            Checkpoint(
                session=opened.id,
                recorded_at=opened.started_at + timedelta(minutes=index),
                note=note,
            )
        )
    return opened


class TestWhatItReports:
    def test_a_checkpoint_from_one_client_reaches_another(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        _session(store, client="claude-code", notes=("fixed the index",))
        signals = provider.collect(_request(), scope)
        rendered = str([signal.value for signal in signals])
        assert "fixed the index" in rendered

    def test_it_says_which_client_wrote_it(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        _session(store, client="claude-code")
        recent = _value(provider, scope, "session.recent")
        assert recent[0]["client"] == "claude-code"

    def test_a_wrapped_session_is_left_to_its_log(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        # A wrapped session is already in the pack as its `activity_log`, found
        # by RECENT_ACTIVITY. Reporting it here too would say the same thing
        # twice and spend budget doing it.
        session = _session(store)
        store.mark_wrapped(session.id, ConceptId.new())
        assert provider.collect(_request(), scope) == ()

    def test_a_session_with_nothing_recorded_says_nothing(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        _session(store, notes=())
        assert provider.collect(_request(), scope) == ()

    def test_newest_first(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        _session(store, client="older", minutes=0)
        _session(store, client="newer", minutes=30)
        recent = _value(provider, scope, "session.recent")
        assert [entry["client"] for entry in recent] == ["newer", "older"]

    def test_another_workspace_is_not_reported(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        elsewhere = Session.opened(
            workspace=ConceptId.new(), actor="a/b", started_at=START, client="claude-code"
        )
        store.open(elsewhere)
        store.append(Checkpoint(session=elsewhere.id, recorded_at=START, note="not here"))
        assert provider.collect(_request(), scope) == ()

    def test_it_is_bounded(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        # A startup pack is bounded by design (core/07). A machine that has run
        # for a year must not push a year of sessions into one.
        for index in range(20):
            _session(store, client=f"c{index}", minutes=index)
        recent = _value(provider, scope, "session.recent")
        assert len(recent) <= 5


class TestWhenItSpeaks:
    def test_startup_wants_it(self, provider: SessionSignalProvider) -> None:
        assert provider.supports(_request(ContextDepth.STARTUP))

    def test_focused_retrieval_does_not(self, provider: SessionSignalProvider) -> None:
        # core/04 section 34: focused retrieval happens inside a session that
        # has already had its startup. Repeating "what happened here recently"
        # on every mid-session retrieval is budget spent on an answer the
        # caller already has.
        assert not provider.supports(_request(ContextDepth.FOCUSED))


class TestProvenance:
    def test_every_signal_says_why_it_is_there(
        self, provider: SessionSignalProvider, store: InMemorySessionStore, scope: ResolvedScope
    ) -> None:
        _session(store)
        for signal in provider.collect(_request(), scope):
            assert signal.reason.is_mechanical
            assert str(signal.reason.code) == str(ReasonCode.RECENT_ACTIVITY)


def _value(
    provider: SessionSignalProvider, scope: ResolvedScope, kind: str
) -> list[dict[str, str]]:
    for signal in provider.collect(_request(), scope):
        if signal.kind == kind:
            assert isinstance(signal.value, list)
            return list(signal.value)
    raise AssertionError(f"no {kind} signal")
