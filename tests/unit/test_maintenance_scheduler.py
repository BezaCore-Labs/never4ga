"""When each maintenance tier runs (`details/data-indexing-maintenance.md` §22).

Three tiers, with cadence from `config.toml` rather than hardcoded. The clock is
injected, like :class:`~never4ga.service.runtime.ReconcileLoop`'s, because a
test that sleeps measures the test runner.

The tiers do not follow §22's list verbatim. The `doctor` rules themselves are
cheap; reading the vault is what costs. So a tier is cheap when it can work from
documents already in hand, not when its rules are cheap:

- **immediate** -- validate the batch the watcher just settled, using the index
  for what the corpus would otherwise supply. Cheap enough to run on every save.
- **frequent** -- holds no rules yet. It is built and configurable so rules
  that belong there have somewhere to land.
- **periodic** -- the full diagnosis into the ledger. Affordable hourly, not
  per save, which is why there is more than one tier.

Nothing here repairs anything. A schedule may detect and may never repair.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from never4ga.services.scheduling import MaintenanceScheduler, Tier

START = 1000.0


class Recorder:
    """What ran, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def immediate(self, paths: Sequence[object]) -> None:
        self.calls.append(f"immediate:{len(paths)}")

    def frequent(self) -> None:
        self.calls.append("frequent")

    def periodic(self) -> None:
        self.calls.append("periodic")


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def scheduler(recorder: Recorder, *, enabled: bool = True) -> MaintenanceScheduler:
    return MaintenanceScheduler(
        immediate=recorder.immediate,
        frequent=recorder.frequent,
        periodic=recorder.periodic,
        frequent_every=60.0,
        periodic_every=600.0,
        started_at=START,
        enabled=enabled,
    )


class TestTheImmediateTier:
    def test_it_runs_when_a_batch_settles(self, recorder: Recorder) -> None:
        scheduler(recorder).after_change(("a.md", "b.md"), now=START + 1)
        assert recorder.calls == ["immediate:2"]

    def test_an_empty_batch_runs_nothing(self, recorder: Recorder) -> None:
        # The watcher can settle with nothing to say. Validating no documents
        # is not a cheap run, it is a pointless one.
        scheduler(recorder).after_change((), now=START + 1)
        assert recorder.calls == []

    def test_it_has_no_cadence_of_its_own(self, recorder: Recorder) -> None:
        # "Immediate" is not a very short interval. It runs on the event or not
        # at all, which is why `[maintenance]` carries no number for it.
        scheduler(recorder).tick(now=START + 0.5)
        assert recorder.calls == []


class TestTheTimedTiers:
    def test_neither_runs_before_its_cadence(self, recorder: Recorder) -> None:
        scheduler(recorder).tick(now=START + 59)
        assert recorder.calls == []

    def test_the_frequent_tier_runs_on_its_cadence(self, recorder: Recorder) -> None:
        scheduler(recorder).tick(now=START + 60)
        assert recorder.calls == ["frequent"]

    def test_the_periodic_tier_runs_on_its_own(self, recorder: Recorder) -> None:
        loop = scheduler(recorder)
        loop.tick(now=START + 600)
        assert recorder.calls == ["frequent", "periodic"]

    def test_a_tier_that_ran_does_not_run_again_immediately(self, recorder: Recorder) -> None:
        loop = scheduler(recorder)
        loop.tick(now=START + 60)
        loop.tick(now=START + 61)
        assert recorder.calls == ["frequent"]

    def test_a_long_gap_runs_a_tier_once_rather_than_catching_up(self, recorder: Recorder) -> None:
        # A laptop that was asleep for a day owes one sweep, not 1,440 of them.
        loop = scheduler(recorder)
        loop.tick(now=START + 86_400)
        assert recorder.calls.count("frequent") == 1


class TestSwitchingItOff:
    @pytest.fixture
    def off(self, recorder: Recorder) -> MaintenanceScheduler:
        return scheduler(recorder, enabled=False)

    def test_no_tier_runs_on_a_timer(self, off: MaintenanceScheduler, recorder: Recorder) -> None:
        off.tick(now=START + 100_000)
        assert recorder.calls == []

    def test_and_none_runs_on_a_change_either(
        self, off: MaintenanceScheduler, recorder: Recorder
    ) -> None:
        off.after_change(("a.md",), now=START + 1)
        assert recorder.calls == []


class TestWhenATierFails:
    """A daemon must not die because one document is malformed.

    The same rule :class:`ReconcileLoop` follows, for the same reason: this runs
    on the service's own thread, and an exception escaping takes the service
    down. The failure is remembered so health output can say so.
    """

    def test_a_failing_tier_does_not_raise(self, recorder: Recorder) -> None:
        def explode() -> None:
            raise RuntimeError("the vault is on fire")

        loop = MaintenanceScheduler(
            immediate=recorder.immediate,
            frequent=explode,
            periodic=recorder.periodic,
            frequent_every=60.0,
            periodic_every=600.0,
            started_at=START,
        )
        loop.tick(now=START + 60)
        assert loop.last_error is not None
        assert "on fire" in loop.last_error

    def test_and_the_other_tiers_still_run(self, recorder: Recorder) -> None:
        def explode() -> None:
            raise RuntimeError("no")

        loop = MaintenanceScheduler(
            immediate=recorder.immediate,
            frequent=explode,
            periodic=recorder.periodic,
            frequent_every=60.0,
            periodic_every=600.0,
            started_at=START,
        )
        loop.tick(now=START + 600)
        assert recorder.calls == ["periodic"]

    def test_a_success_clears_the_error(self, recorder: Recorder) -> None:
        failing = [True]

        def sometimes() -> None:
            if failing[0]:
                raise RuntimeError("not yet")

        loop = MaintenanceScheduler(
            immediate=recorder.immediate,
            frequent=sometimes,
            periodic=recorder.periodic,
            frequent_every=60.0,
            periodic_every=600.0,
            started_at=START,
        )
        loop.tick(now=START + 60)
        failing[0] = False
        loop.tick(now=START + 120)
        assert loop.last_error is None

    def test_a_failing_immediate_tier_does_not_raise_either(self, recorder: Recorder) -> None:
        def explode(paths: Sequence[object]) -> None:
            raise RuntimeError("mid-save")

        loop = MaintenanceScheduler(
            immediate=explode,
            frequent=recorder.frequent,
            periodic=recorder.periodic,
            frequent_every=60.0,
            periodic_every=600.0,
            started_at=START,
        )
        loop.after_change(("a.md",), now=START + 1)
        assert loop.last_error is not None


class TestWhatRan:
    def test_tick_reports_the_tiers_it_ran(self, recorder: Recorder) -> None:
        assert scheduler(recorder).tick(now=START + 600) == (Tier.FREQUENT, Tier.PERIODIC)

    def test_and_reports_nothing_when_nothing_was_due(self, recorder: Recorder) -> None:
        assert scheduler(recorder).tick(now=START + 1) == ()
