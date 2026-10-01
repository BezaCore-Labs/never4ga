"""Which standards a startup reads in full (core/07 section 10).

A standard's location says who it binds (core/02 section 21.15), and
`required_reading` says when. A vault-wide standard, in `50_System/Standards/`,
binds every session and is required reading unless it says
`required_reading: false`. A workspace's own standard binds the work it
describes and is read on demand unless it says `required_reading: true`.

A standard that `excepts` a required one is required too: a session that read
the rule in full and missed the exception that overrules it would apply the
wrong rule.

The startup pack and `doctor`'s ceiling report both ask this module, so the two
cannot disagree about what a session has to read.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["StandardFacts", "excepted_targets", "required_standards"]


@dataclass(frozen=True, slots=True)
class StandardFacts:
    """What deciding a standard's reading needs, from either the index or the vault."""

    concept_id: str
    vault_wide: bool
    #: The field as written. Anything but `True` or `False` is treated as
    #: absent; validation reports it rather than this guessing.
    required_reading: object = None
    #: Targets of the standard's `excepts` relations.
    excepts: tuple[str, ...] = ()


def excepted_targets(relations: object) -> tuple[str, ...]:
    """The targets of the `excepts` relations in a `relations` field as written."""
    if not isinstance(relations, list):
        return ()
    return tuple(
        str(relation["target"])
        for relation in relations
        if isinstance(relation, dict)
        and relation.get("type") == "excepts"
        and relation.get("target") is not None
    )


def _by_default(standard: StandardFacts) -> bool:
    if isinstance(standard.required_reading, bool):
        return standard.required_reading
    return standard.vault_wide


def required_standards(standards: Iterable[StandardFacts]) -> frozenset[str]:
    """The ids of the standards, among those that apply, that a startup reads in full."""
    candidates = list(standards)
    required = {standard.concept_id for standard in candidates if _by_default(standard)}
    # An exception to an exception follows too, so repeat until nothing moves.
    while True:
        following = {
            standard.concept_id
            for standard in candidates
            if standard.concept_id not in required
            and any(target in required for target in standard.excepts)
        }
        if not following:
            return frozenset(required)
        required |= following
