"""Retrieval metrics, and the honesty constraint on reporting them.

`core/06` section 9 lists what a backend is measured on. This corpus supports
four of those -- Recall@k, the rank of the first required result, MRR and
latency -- plus per-lane contribution, so that a bad number is diagnosable
rather than merely bad.

The part worth reading is :func:`compare`. A corpus of twenty cases moves mean
Recall@k by five whole points when a single case flips, so a difference smaller
than one case is not evidence of anything. The harness says that rather than
report the difference, because a question like "does an embedder make retrieval
better" is exactly the kind a small corpus can appear to answer while answering
nothing.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = [
    "CaseResult",
    "Comparison",
    "CorpusReport",
    "compare",
    "first_required_rank",
    "recall_at_k",
    "reciprocal_rank",
]


def recall_at_k(ranking: Sequence[str], required: Sequence[str], k: int) -> float:
    """How much of what a case required appears in the first ``k`` results.

    A case that requires nothing cannot be scored. Returning 1.0 for it would
    let an empty expectation raise the corpus mean, which is the one way a
    retrieval eval can flatter itself without anyone noticing.
    """
    if not required:
        raise ValueError("a case requires at least one document to be scored")
    found = set(ranking[:k])
    return sum(1 for document in required if document in found) / len(required)


def first_required_rank(ranking: Sequence[str], required: Sequence[str]) -> int | None:
    """The 1-based rank of the first required document, or None if absent.

    1-based because it is read by a person deciding whether a result was where
    they would have looked.
    """
    wanted = set(required)
    for position, document in enumerate(ranking, start=1):
        if document in wanted:
            return position
    return None


def reciprocal_rank(ranking: Sequence[str], required: Sequence[str]) -> float:
    rank = first_required_rank(ranking, required)
    return 0.0 if rank is None else 1 / rank


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One case, run once, against one configuration."""

    case_id: str
    ranking: tuple[str, ...]
    required: tuple[str, ...]
    forbidden: tuple[str, ...]
    deprioritised: tuple[str, ...]
    latency_ms: float
    lanes: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "lanes", MappingProxyType(dict(self.lanes)))

    def recall_at(self, k: int) -> float:
        return recall_at_k(self.ranking, self.required, k)

    @property
    def first_required(self) -> int | None:
        return first_required_rank(self.ranking, self.required)

    @property
    def reciprocal_rank(self) -> float:
        return reciprocal_rank(self.ranking, self.required)

    def violations(self, k: int) -> tuple[str, ...]:
        """Every expectation this case stated and this ranking broke.

        Reported rather than counted: `core/02` section 23's habit, and the
        thing a person needs in order to decide whether the case or the
        retrieval was wrong.
        """
        found = set(self.ranking[:k])
        problems = [
            f"required {document} is missing from the first {k}"
            for document in self.required
            if document not in found
        ]
        # Judged at k, like the recall it sits beside. Retrieval is measured
        # before the budget and the lexical lane ORs its terms, so the tail of
        # a ranking holds documents that matched one common word -- which
        # `terms_from_task` accepts by design and a case must not fail for.
        problems.extend(
            f"forbidden {document} appears at rank {self.ranking.index(document) + 1}"
            for document in self.forbidden
            if document in found
        )
        last_required = max(
            (self.ranking.index(document) for document in self.required if document in found),
            default=None,
        )
        if last_required is not None:
            problems.extend(
                f"{document} outranks a required document"
                for document in self.deprioritised
                if document in self.ranking and self.ranking.index(document) < last_required
            )
        return tuple(problems)


@dataclass(frozen=True, slots=True)
class CorpusReport:
    """A whole corpus, run once. Built through :meth:`of`."""

    results: tuple[CaseResult, ...]
    k: int
    recall: float
    mrr: float
    median_latency_ms: float
    slowest_latency_ms: float
    lanes: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "lanes", MappingProxyType(dict(self.lanes)))

    @classmethod
    def of(cls, results: Sequence[CaseResult], k: int) -> CorpusReport:
        if not results:
            raise ValueError(
                "a corpus with no ratified cases measures nothing; "
                "ratify at least one case before reporting"
            )
        latencies = [result.latency_ms for result in results]
        lanes: Counter[str] = Counter()
        for result in results:
            lanes.update(result.lanes)
        return cls(
            results=tuple(results),
            k=k,
            recall=statistics.fmean(result.recall_at(k) for result in results),
            mrr=statistics.fmean(result.reciprocal_rank for result in results),
            median_latency_ms=statistics.median(latencies),
            slowest_latency_ms=max(latencies),
            lanes=dict(lanes),
        )

    @property
    def case_count(self) -> int:
        return len(self.results)

    @property
    def single_case_swing(self) -> float:
        """How far mean Recall@k moves if one case goes from total miss to total hit.

        The corpus's resolution, and the number every reported difference is
        held against.
        """
        return 1 / self.case_count

    @property
    def failures(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple(
            (result.case_id, violations)
            for result in self.results
            if (violations := result.violations(self.k))
        )


@dataclass(frozen=True, slots=True)
class Comparison:
    """One configuration against another, over the same cases."""

    delta: float
    single_case_swing: float
    distinguishable: bool
    verdict: str


def compare(baseline: CorpusReport, candidate: CorpusReport) -> Comparison:
    """Whether the corpus can tell two configurations apart at all.

    Refuses to compare runs that are not comparable, because a baseline is only
    a baseline against the same cases at the same ``k``.
    """
    if baseline.k != candidate.k:
        raise ValueError(
            f"a comparison needs the same k on both sides; got {baseline.k} and {candidate.k}"
        )
    if [result.case_id for result in baseline.results] != [
        result.case_id for result in candidate.results
    ]:
        raise ValueError("a comparison needs the same cases on both sides, in the same order")

    delta = candidate.recall - baseline.recall
    swing = baseline.single_case_swing
    # The boundary belongs to "cannot tell". A delta of exactly one case's swing
    # is one case flipping, and floating-point summation over the case means
    # will land either side of it for the same corpus -- so a strict `>` would
    # decide the comparison on arithmetic noise.
    distinguishable = abs(delta) > swing and not math.isclose(abs(delta), swing, rel_tol=1e-9)
    if distinguishable:
        verdict = (
            f"Recall@{baseline.k} moved {delta:+.3f}, more than the {swing:.3f} "
            f"one case can account for across {baseline.case_count} cases"
        )
    else:
        verdict = (
            f"Recall@{baseline.k} moved {delta:+.3f}, which one case flipping "
            f"could account for ({swing:.3f} across {baseline.case_count} cases). "
            "This corpus cannot tell these configurations apart"
        )
    return Comparison(
        delta=delta,
        single_case_swing=swing,
        distinguishable=distinguishable,
        verdict=verdict,
    )
