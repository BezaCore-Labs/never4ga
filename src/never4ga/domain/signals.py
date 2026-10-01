"""Structured context signals (core/07 section 4).

A signal is a *fact*, not a sentence. core/07 section 9 wants mechanical
summaries -- a branch name, a ticket status, a count of accepted decisions --
and section 1 reserves prose for optional enrichment behind separate interfaces.

That distinction is enforced here rather than left to convention: a value must
be a scalar or a small structure of scalars, single-line and short, and the
acquisition reason must be mechanical. A provider that needs a model is not a
ContextSignalProvider.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from never4ga.domain.provenance import AcquisitionReason

__all__ = ["MAX_SIGNAL_TEXT_LENGTH", "ContextSignal", "SignalValue"]

#: Longer than any identifier, branch, status or title fragment; far shorter
#: than a generated paragraph.
MAX_SIGNAL_TEXT_LENGTH = 120

type SignalValue = (
    str | int | float | bool | Sequence["SignalValue"] | Mapping[str, "SignalValue"] | None
)


def _validate_structured(value: object, *, depth: int = 0) -> None:
    if depth > 3:
        raise ValueError("context signal values must be structured, not deeply nested")
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise ValueError("context signal values must be structured, not multi-line prose")
        if len(value) > MAX_SIGNAL_TEXT_LENGTH:
            raise ValueError(
                f"context signal values must be structured; {len(value)} characters "
                f"exceeds the {MAX_SIGNAL_TEXT_LENGTH}-character limit"
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("context signal mappings must be keyed by strings")
            _validate_structured(item, depth=depth + 1)
        return
    if isinstance(value, Sequence):
        for item in value:
            _validate_structured(item, depth=depth + 1)
        return
    raise ValueError(f"context signal values must be structured; {type(value).__name__} is not")


@dataclass(frozen=True, slots=True)
class ContextSignal:
    """One structured fact contributed by a :class:`ContextSignalProvider`."""

    provider_id: str
    kind: str
    value: SignalValue
    reason: AcquisitionReason

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("context signal requires a provider_id")
        if not self.kind.strip():
            raise ValueError("context signal requires a kind")
        if not self.reason.is_mechanical:
            raise ValueError(
                f"context signals are mechanical; {self.reason.stage.value!r} belongs "
                "behind an enrichment interface"
            )
        _validate_structured(self.value)
