"""ContextSignalProvider -- mechanical signal contribution (core/07 section 4).

A provider contributes structured signals without owning Context Pack assembly.
Providers are optional and capability-driven: ``supports`` lets a provider
decline a request it has nothing to say about, so that no cost is paid for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from never4ga.domain.context import ContextRequest
from never4ga.domain.scope import ResolvedScope
from never4ga.domain.signals import ContextSignal

__all__ = ["ContextSignalProvider"]


@runtime_checkable
class ContextSignalProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def supports(self, request: ContextRequest) -> bool:
        """Whether this provider has anything to contribute to this request."""
        ...

    def collect(
        self,
        request: ContextRequest,
        scope: ResolvedScope,
    ) -> Sequence[ContextSignal]:
        """Return structured signals. Must not require a model call."""
        ...
