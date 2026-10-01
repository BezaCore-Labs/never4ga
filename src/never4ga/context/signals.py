"""Collecting external operational signals (core/07 Stage F).

Signals are the one part of assembly that reaches outside the vault (Git, a
work-management tracker), so it is the one part that
can fail for reasons that have nothing to do with the vault. It degrades rather
than failing: a pack without Git state is still a pack, and a session that gets
nothing because a provider threw has lost far more than a branch name.

Every assembler collects the same way, so the rule lives here once.
"""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.context import ContextRequest
from never4ga.domain.scope import ResolvedScope
from never4ga.domain.signals import ContextSignal
from never4ga.ports.context_signal_provider import ContextSignalProvider

__all__ = ["collect_signals"]


def collect_signals(
    providers: Sequence[ContextSignalProvider],
    request: ContextRequest,
    scope: ResolvedScope,
) -> tuple[tuple[ContextSignal, ...], tuple[str, ...]]:
    """Every provider's signals, and the id of each provider that failed."""
    signals: list[ContextSignal] = []
    degraded: list[str] = []
    for provider in providers:
        try:
            if not provider.supports(request):
                continue
            signals.extend(provider.collect(request, scope))
        except Exception:
            degraded.append(provider.provider_id)
    return tuple(signals), tuple(degraded)
