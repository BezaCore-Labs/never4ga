"""Eval cases: the fixture shape from `details/retrieval-context-memory.md` section 22.

    Given:   workspace = X, task = "..."
    Expect:  includes A, includes B, excludes C, does not prioritise D

Cases are TOML because `tomllib` is stdlib and because a person reads and edits
them: a case file is a queue of expectations waiting to be ratified, not an
internal serialisation.

**Ratification is over the expectations, not over the case.** A case names a
`ratified_hash` covering exactly the lines a person had to agree with -- the
requirement, the exclusion and the de-prioritisation. Editing any of them
invalidates the stamp and the case stops counting until somebody reads it again.
Editing the prose around them does not, because prose is not run.

The alternative -- a boolean `ratified = true` -- would let a later edit inherit
an approval nobody gave it.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = [
    "CaseFormatError",
    "EvalCase",
    "Ratification",
    "dangling_references",
    "load_cases",
    "ratified",
]

#: Vector retrieval may join focused and deep context but never startup, so a
#: case that asked for a startup pack would measure a lane it must not touch.
_DEPTHS = ("focused", "deep")


class CaseFormatError(Exception):
    """A case file that cannot be read as written. Never repaired, only reported."""


class Ratification(Enum):
    DRAFT = "draft"
    RATIFIED = "ratified"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One retrieval expectation, and whether anybody has agreed to it."""

    case_id: str
    task: str
    workspace: str
    depth: str
    requires: tuple[str, ...]
    forbids: tuple[str, ...]
    deprioritises: tuple[str, ...]
    why: str
    ratification: Ratification
    ratification_note: str
    expectation_hash: str
    #: Who agreed to the expectations. A person's name is a relevance judgement over
    #: a real vault; "construction" means the synthetic vault was written so
    #: that the expectation is true, which is a regression fixture and not a
    #: judgement about relevance. A recorded baseline names it either way.
    ratified_by: str = ""

    @property
    def counts(self) -> bool:
        return self.ratification is Ratification.RATIFIED


def _expectation_hash(
    requires: Sequence[str], forbids: Sequence[str], deprioritises: Sequence[str], task: str
) -> str:
    """A stamp over exactly what a person had to judge.

    The task is in it: the same expectations against a different question are a
    different judgement. Sorted and JSON-encoded so that reordering a list --
    which changes nothing a person agreed to -- does not revoke the stamp.
    """
    payload = json.dumps(
        {
            "task": task,
            "requires": sorted(requires),
            "forbids": sorted(forbids),
            "deprioritises": sorted(deprioritises),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_cases(path: Path) -> tuple[EvalCase, ...]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise CaseFormatError(f"{path} is not readable as TOML: {error}") from error

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for index, entry in enumerate(document.get("case", ())):
        case = _case(entry, path, index)
        if case.case_id in seen:
            raise CaseFormatError(f"{path} defines {case.case_id!r} more than once")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def _case(entry: dict[str, object], path: Path, index: int) -> EvalCase:
    def _text(key: str, *, required: bool = True) -> str:
        value = entry.get(key, "")
        if not isinstance(value, str) or (required and not value):
            raise CaseFormatError(f"{path} case {index} needs a non-empty {key!r}")
        return value

    def _list(key: str) -> tuple[str, ...]:
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise CaseFormatError(f"{path} case {index}: {key!r} must be a list of vault paths")
        return tuple(str(item) for item in value)

    case_id = _text("id")
    task = _text("task")
    depth = _text("depth")
    if depth not in _DEPTHS:
        raise CaseFormatError(
            f"{path} case {case_id!r} asks for {depth!r} context; cases run at "
            f"{' or '.join(_DEPTHS)} only, because vectors stay out of startup"
        )

    requires, forbids, deprioritises = _list("requires"), _list("forbids"), _list("deprioritises")
    if not requires:
        raise CaseFormatError(
            f"{path} case {case_id!r} requires nothing; a case that expects no "
            "document cannot be scored and would raise the corpus mean for free"
        )
    if overlap := set(requires) & set(forbids):
        raise CaseFormatError(
            f"{path} case {case_id!r} lists {', '.join(sorted(overlap))} as both "
            "required and forbidden"
        )

    expectation_hash = _expectation_hash(requires, forbids, deprioritises, task)
    stamped = entry.get("ratified_hash")
    ratified_by = ""
    if not isinstance(stamped, str) or not stamped:
        state, note = Ratification.DRAFT, "drafted, and not yet ratified by anybody"
    elif stamped == expectation_hash:
        state = Ratification.RATIFIED
        ratified_by = _text("ratified_by")
        note = f"ratified by {ratified_by} on {_text('ratified_at')}"
    else:
        state = Ratification.STALE
        note = (
            f"the expectations changed since this was ratified "
            f"(stamped {stamped}, now {expectation_hash}); "
            f"somebody has to re-read the case and re-stamp it"
        )

    return EvalCase(
        case_id=case_id,
        task=task,
        workspace=_text("workspace"),
        depth=depth,
        requires=requires,
        forbids=forbids,
        deprioritises=deprioritises,
        why=_text("why", required=False),
        ratification=state,
        ratification_note=note,
        expectation_hash=expectation_hash,
        ratified_by=ratified_by,
    )


def ratified(cases: Sequence[EvalCase]) -> tuple[EvalCase, ...]:
    """Only the cases somebody agreed to. Everything else is a draft in a queue."""
    return tuple(case for case in cases if case.counts)


def dangling_references(cases: Sequence[EvalCase], vault: Path) -> tuple[str, ...]:
    """Every expectation naming a document the vault does not have.

    An expectation about a document that does not exist cannot be wrong, and
    the two that cannot be wrong are the dangerous ones. A `forbids` on a
    missing document can never fire, so the case banks the credit for free; a
    `deprioritises` scores by rank, and a document that cannot be retrieved can
    never be ranked badly. Both read as a pass.

    When a vault deletes a document that a case forbids, the case goes inert
    and keeps reporting green without anybody noticing.

    A missing `requires` is checked too, though it fails loudly on its own. It
    fails as a **recall of zero**, which reads as retrieval being bad at the
    question rather than as the question naming a document nobody has, and
    saying which costs one line.

    A real-vault case file lives in the vault it judges, so it depends on that
    vault's mutable content by construction: the judgements are personal and
    cannot be checked into a repository anybody clones. Nothing here changes
    that. What it changes is that the dependency breaks loudly.
    """
    dangling: list[str] = []
    for case in cases:
        for kind in ("requires", "forbids", "deprioritises"):
            for reference in getattr(case, kind):
                if not (vault / reference).exists():
                    dangling.append(
                        f"{case.case_id}: {kind} {reference}, which is not in the vault"
                    )
    return tuple(dangling)
