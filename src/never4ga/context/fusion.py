"""Rank fusion (core/06 section 7).

"Hybrid retrieval orchestration belongs in Never4gA." Three retrievers produce
values that cannot be compared -- a BM25 score, a metadata match, a graph hop
distance -- and :class:`~never4ga.domain.retrieval.RetrievalCandidate` says so
outright: it defines no ordering, because "fusion must work from rank".

Reciprocal Rank Fusion does exactly that. Each retriever contributes
``1 / (k + rank)`` for the documents it returned, and the contributions are
summed. Nothing is normalised, because ranks are already on the same scale.
Nothing is weighted, because there is no evaluation to tune weights against,
and guessed weights that quietly become load-bearing are worse than a scheme
with no weights at all.

The property that makes fusion worth doing at all: a document two retrievers
found independently outranks one that a single retriever put first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.retrieval import RetrievalCandidate

__all__ = ["DEFAULT_K", "FusedCandidate", "reciprocal_rank_fusion"]

#: The conventional RRF constant. It damps the difference between the top few
#: ranks so that one retriever's confident first place cannot outweigh agreement
#: among several -- and RRF is famously insensitive to its exact value, which is
#: the point of having only one.
DEFAULT_K: Final = 60


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One document, and how every retriever that found it voted.

    A concept, or a note in foreign material (core/02 section 3.3): then
    ``concept_id`` is ``None`` and ``source_path`` is the path that owns it,
    which is derived and never an identity. Exactly one of the two is set.
    """

    concept_id: ConceptId | None
    score: float
    contributions: Mapping[str, int]
    reasons: tuple[AcquisitionReason, ...]
    source_path: VaultPath | None = None

    def __post_init__(self) -> None:
        if (self.concept_id is None) == (self.source_path is None):
            raise ValueError("a fused candidate is owned by a concept or a path, not both")
        object.__setattr__(self, "contributions", MappingProxyType(dict(self.contributions)))

    @property
    def owner(self) -> str:
        """The concept's id, or ``path:<path>``, as a chunk names its owner."""
        if self.concept_id is not None:
            return str(self.concept_id)
        return f"path:{self.source_path}"


def reciprocal_rank_fusion(
    candidates: Iterable[RetrievalCandidate],
    k: int = DEFAULT_K,
) -> tuple[FusedCandidate, ...]:
    """Fuse candidates from any number of retrievers into one ranked list.

    A retriever votes once per document, at its best rank: a document that
    matched several chunks must not out-vote itself simply for being long.
    """
    best: dict[str, dict[str, int]] = {}
    reasons: dict[str, dict[str, AcquisitionReason]] = {}
    owners: dict[str, tuple[ConceptId | None, VaultPath | None]] = {}

    owner: tuple[ConceptId | None, VaultPath | None]
    for candidate in candidates:
        # Grouped by owner: a concept by its id, a foreign note by its path.
        # The prefix keeps the two apart, so no path
        # can vote for a concept or the other way round.
        if candidate.concept_id is not None:
            owner, key = (candidate.concept_id, None), str(candidate.concept_id)
        else:
            owner, key = (None, candidate.source_path), f"path:{candidate.source_path}"
        owners.setdefault(key, owner)
        ranks = best.setdefault(key, {})
        previous = ranks.get(candidate.retriever)
        if previous is None or candidate.rank < previous:
            ranks[candidate.retriever] = candidate.rank
            reasons.setdefault(key, {})[candidate.retriever] = _reason(candidate)

    fused = [
        FusedCandidate(
            concept_id=owners[key][0],
            source_path=owners[key][1],
            score=sum(1 / (k + rank) for rank in ranks.values()),
            contributions=ranks,
            reasons=tuple(reasons[key][retriever] for retriever in sorted(ranks)),
        )
        for key, ranks in best.items()
    ]
    # Ties break on identity so that the same inputs always produce the same
    # order, whatever order the retrievers answered in.
    return tuple(sorted(fused, key=lambda item: (-item.score, item.owner)))


def _reason(candidate: RetrievalCandidate) -> AcquisitionReason:
    """Why the candidate is here; for a foreign note, that it is one.

    The record says so in its reason (core/02 section 3.3), so nobody reads a
    path as an identity. The lane's own reason survives in the detail.
    """
    if candidate.concept_id is not None:
        return candidate.reason
    lane = candidate.reason
    return AcquisitionReason.of(
        ReasonCode.FOREIGN_MATERIAL, detail=f"{lane.code} {lane.detail}".strip()
    )
