"""Reciprocal Rank Fusion (core/06 section 7).

The three retrievers produce values that are not comparable -- a BM25 score, a
metadata match, a graph hop distance -- and `RetrievalCandidate` says so in as
many words: "fusion must work from rank". RRF does, which is why it needs no
normalisation and has nothing corpus-specific to calibrate, unlike a weighted
combination.
"""

from __future__ import annotations

from never4ga.context.fusion import DEFAULT_K, reciprocal_rank_fusion
from never4ga.domain.chunk import ChunkIdentity
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, AcquisitionStage, ReasonCode
from never4ga.domain.retrieval import RetrievalCandidate

A = ConceptId.new()
B = ConceptId.new()
C = ConceptId.new()


def candidate(concept_id: ConceptId, retriever: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        concept_id=concept_id,
        retriever=retriever,
        rank=rank,
        reason=AcquisitionReason.of(ReasonCode.FTS_RANK, detail=f"rank {rank}"),
    )


def order(*candidates: RetrievalCandidate) -> list[ConceptId | None]:
    return [fused.concept_id for fused in reciprocal_rank_fusion(candidates)]


class TestFusion:
    def test_nothing_fuses_to_nothing(self) -> None:
        assert reciprocal_rank_fusion([]) == ()

    def test_a_single_retriever_keeps_its_order(self) -> None:
        assert order(candidate(A, "fts", 1), candidate(B, "fts", 2)) == [A, B]

    def test_agreement_between_retrievers_outranks_a_single_first_place(self) -> None:
        # The whole reason to fuse: two retrievers finding the same document
        # independently is stronger evidence than one ranking it first.
        result = order(
            candidate(A, "fts", 1),
            candidate(B, "fts", 2),
            candidate(B, "metadata", 1),
            candidate(B, "graph", 1),
        )
        assert result[0] == B

    def test_scores_are_summed_across_retrievers(self) -> None:
        fused = reciprocal_rank_fusion([candidate(A, "fts", 1), candidate(A, "graph", 1)])
        assert fused[0].score == 2 / (DEFAULT_K + 1)

    def test_the_best_rank_per_retriever_counts_once(self) -> None:
        # A document matching several chunks must not out-vote itself: one
        # retriever gets one vote, at its best rank.
        fused = reciprocal_rank_fusion(
            [candidate(A, "fts", 1), candidate(A, "fts", 2), candidate(A, "fts", 3)]
        )
        assert len(fused) == 1
        assert fused[0].score == 1 / (DEFAULT_K + 1)

    def test_every_contributing_retriever_is_recorded(self) -> None:
        fused = reciprocal_rank_fusion([candidate(A, "fts", 3), candidate(A, "graph", 1)])
        assert fused[0].contributions == {"fts": 3, "graph": 1}

    def test_a_lower_rank_contributes_less(self) -> None:
        [first] = reciprocal_rank_fusion([candidate(A, "fts", 1)])
        [tenth] = reciprocal_rank_fusion([candidate(A, "fts", 10)])
        assert first.score > tenth.score

    def test_ties_break_deterministically(self) -> None:
        first = order(candidate(A, "fts", 1), candidate(B, "metadata", 1))
        second = order(candidate(B, "metadata", 1), candidate(A, "fts", 1))
        assert first == second

    def test_no_normalisation_is_needed_across_incomparable_scores(self) -> None:
        # A BM25 score of 0.02 and a graph distance of 1 never meet: only ranks do.
        wildly_scored = RetrievalCandidate(
            concept_id=A,
            retriever="fts",
            rank=1,
            reason=AcquisitionReason.of(ReasonCode.FTS_RANK),
            provider_score=0.0001,
        )
        graph = RetrievalCandidate(
            concept_id=B,
            retriever="graph",
            rank=1,
            reason=AcquisitionReason.of(ReasonCode.RELATION_DEPTH),
            provider_score=9999.0,
        )
        fused = reciprocal_rank_fusion([wildly_scored, graph])
        assert fused[0].score == fused[1].score

    def test_a_reason_survives_fusion(self) -> None:
        # core/07 section 12: an item must still be able to say why it is here.
        fused = reciprocal_rank_fusion([candidate(A, "fts", 2)])
        assert fused[0].reasons[0].is_mechanical

    def test_k_is_adjustable_but_defaults_to_sixty(self) -> None:
        assert DEFAULT_K == 60
        [fused] = reciprocal_rank_fusion([candidate(A, "fts", 1)], k=1)
        assert fused.score == 0.5


NOTE = VaultPath.parse("Projects/note.md")


def foreign(path: VaultPath, rank: int, *, ordinal: int = 0) -> RetrievalCandidate:
    return RetrievalCandidate(
        concept_id=None,
        retriever="fts",
        rank=rank,
        reason=AcquisitionReason.of(ReasonCode.FTS_RANK, detail=f"rank {rank}"),
        chunk=ChunkIdentity(
            concept_id=None,
            source=path,
            heading_path=(),
            ordinal=ordinal,
            content_hash="h",
            policy_version="p",
        ),
        source_path=path,
    )


class TestForeignNotes:
    """A foreign note is fused by its path, never an identity (core/02 section 3.3)."""

    def test_a_foreign_note_is_fused_under_its_path(self) -> None:
        (fused,) = reciprocal_rank_fusion([foreign(NOTE, 1)])
        assert fused.concept_id is None
        assert fused.source_path == NOTE

    def test_it_ranks_among_concepts_by_what_the_lane_said(self) -> None:
        fused = reciprocal_rank_fusion([candidate(A, "fts", 1), foreign(NOTE, 2)])
        assert [item.concept_id for item in fused] == [A, None]
        assert fused[1].source_path == NOTE

    def test_its_reason_says_it_is_foreign_material(self) -> None:
        # The record says so in its reason, and keeps what the lane said about
        # how it matched.
        (fused,) = reciprocal_rank_fusion([foreign(NOTE, 3)])
        (reason,) = fused.reasons
        assert reason.code == ReasonCode.FOREIGN_MATERIAL
        assert reason.stage == AcquisitionStage.LEXICAL
        assert reason.is_mechanical
        assert reason.detail == "fts_rank rank 3"

    def test_a_long_note_votes_once(self) -> None:
        fused = reciprocal_rank_fusion([foreign(NOTE, 1), foreign(NOTE, 2, ordinal=1)])
        assert len(fused) == 1
        assert fused[0].contributions == {"fts": 1}

    def test_two_notes_are_two_results(self) -> None:
        other = VaultPath.parse("Reading/note.md")
        fused = reciprocal_rank_fusion([foreign(NOTE, 1), foreign(other, 2)])
        assert [item.source_path for item in fused] == [NOTE, other]

    def test_a_concept_never_carries_a_source_path(self) -> None:
        (fused,) = reciprocal_rank_fusion([candidate(A, "fts", 1)])
        assert fused.source_path is None
