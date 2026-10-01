"""When maintenance runs. `details/data-indexing-maintenance.md` section 22.

Section 22 divides maintenance into three tiers -- immediate on change, frequent
lightweight, periodic heavier -- and says the cadence is configurable rather
than hardcoded. It runs **in-process on the service that already exists**, not
on a systemd timer. The service already owns the index, already watches the
vault, and already holds the immediate tier by construction; a timer would be a
second thing to find the vault, a second writer to the index, and a second
thing to install under ``~/.config/systemd/user/``.

The cost is stated rather than hidden: **nothing runs when the service is not
running.** That is consistent with `core/05` section 19 -- nothing may *depend*
on the service -- but it makes scheduled maintenance best-effort, which is why
``doctor`` remains a complete standalone diagnosis. A person who never runs the
service loses timeliness and nothing else.

**Section 22's tier contents are not taken verbatim.** It sorts rules by how
heavy they sound, but no ``doctor`` rule is expensive: nearly all the time in a
diagnosis goes to reading and parsing documents. So a tier is cheap exactly when
it can work from documents already in hand:

``immediate``
    The batch the watcher just settled, validated against ids the index already
    holds -- cheap enough to run on every save.

``frequent``
    Nothing yet. That is a fact about which rules exist, not a design choice:
    section 22 lists stale deadlines (part of the full diagnosis), connection
    health and adapter drift, and unresolved relation targets (the index
    computes these on every reconcile). The tier is built and configurable so
    such rules have somewhere to land; adding a tier later would change
    ``config.toml`` under anyone who had already written one.

``periodic``
    The full diagnosis, recorded in the ledger. It is affordable hourly and not
    per save, which is the entire reason there is more than one tier.

**Nothing here repairs anything.** The schedule may detect on a timer and may
never repair on one. Repair happens because somebody typed ``--apply``.

This module holds policy, never adapters. What each tier *does* is passed in, so
the composition root stays the only thing that knows a session exists -- and the
cadences arrive as numbers rather than as a :class:`LocalConfig`, because
`core/05` section 15 lets only a composition root read the config file. A
service that took its own settings from disk would stop being a function of the
arguments it was given, and `tests/architecture/test_layering.py` says so.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any

__all__ = ["MaintenanceScheduler", "Tier"]


class Tier(Enum):
    """Section 22's three, named so a caller can say which one ran."""

    IMMEDIATE = "immediate"
    FREQUENT = "frequent"
    PERIODIC = "periodic"


class MaintenanceScheduler:
    """Runs each tier when it is due, and never raises.

    Nothing here raises for :class:`~never4ga.service.runtime.ReconcileLoop`'s
    reason: it runs on the service's own thread, and an exception escaping would
    take the daemon down because one document was malformed. A failure is
    remembered for health output instead, and a later success clears it.
    """

    def __init__(
        self,
        *,
        immediate: Callable[[Sequence[Any]], Any],
        frequent: Callable[[], Any],
        periodic: Callable[[], Any],
        frequent_every: float,
        periodic_every: float,
        started_at: float,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._immediate = immediate
        self._runs: dict[Tier, Callable[[], Any]] = {
            Tier.FREQUENT: frequent,
            Tier.PERIODIC: periodic,
        }
        self._intervals = {
            Tier.FREQUENT: frequent_every,
            Tier.PERIODIC: periodic_every,
        }
        self._last = dict.fromkeys(self._runs, started_at)
        self._last_error: str | None = None
        self._lock = threading.Lock()

    @property
    def last_error(self) -> str | None:
        """The most recent failure, for health output. Cleared by a success."""
        return self._last_error

    def after_change(self, paths: Sequence[Any], *, now: float) -> tuple[Tier, ...]:
        """The immediate tier: the batch the watcher settled.

        **The caller must have indexed the batch already.** Validating a new
        document against a set of ids that predates it reports every relation
        into it as broken, and nothing here can check that the caller got the
        order right.

        An empty batch runs nothing. The watcher can settle with nothing to
        say, and validating no documents is pointless rather than cheap.
        """
        if not self._enabled or not paths:
            return ()
        self._attempt(lambda: self._immediate(paths))
        return (Tier.IMMEDIATE,)

    def tick(self, *, now: float) -> tuple[Tier, ...]:
        """Run whichever timed tiers are due. Returns which ones ran.

        The immediate tier is absent by construction: "immediate" is an event,
        not a very short interval, which is why ``[maintenance]`` carries no
        number for it.
        """
        if not self._enabled:
            return ()
        ran = []
        for tier in (Tier.FREQUENT, Tier.PERIODIC):
            with self._lock:
                if now - self._last[tier] < self._intervals[tier]:
                    continue
                # Stamped to now rather than advanced by the interval: a laptop
                # that was asleep for a day owes one sweep, not 1,440 of them.
                self._last[tier] = now
            self._attempt(self._runs[tier])
            ran.append(tier)
        return tuple(ran)

    def _attempt(self, run: Callable[[], Any]) -> None:
        try:
            run()
        # Broad on purpose: the daemon outlives one bad run.
        except Exception as error:
            self._last_error = str(error)
            return
        self._last_error = None
