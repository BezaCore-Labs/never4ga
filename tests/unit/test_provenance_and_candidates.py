"""Acquisition provenance and backend-neutral retrieval candidates.

Specification:
- core/07 section 12 -- every Context Pack item identifies how it arrived.
- core/07 section 8 -- mechanical selection is distinguishable from model-derived inference.
- core/06 section 6 -- retrievers return normalised candidates; provider_score is diagnostic.
- core/06 section 21 -- backends declare capabilities instead of being named.
"""

from __future__ import annotations

import pytest

from never4ga.domain.capabilities import (
    GraphIndexCapability,
    TextIndexCapability,
    VectorIndexCapability,
    require_capability,
)
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, AcquisitionStage, ReasonCode
from never4ga.domain.retrieval import RetrievalCandidate
from never4ga.errors import CapabilityNotSupportedError


class TestAcquisitionReason:
    def test_known_codes_carry_their_stage(self) -> None:
        assert AcquisitionReason.of(ReasonCode.FTS_RANK).stage is AcquisitionStage.LEXICAL
        assert AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED).stage is AcquisitionStage.SCOPE
        assert AcquisitionReason.of(ReasonCode.RELATION_DEPTH).stage is AcquisitionStage.RELATION
        assert AcquisitionReason.of(ReasonCode.GIT_CHANGED_FILE).stage is (
            AcquisitionStage.EXTERNAL_STATE
        )
        assert AcquisitionReason.of(ReasonCode.VECTOR_RANK).stage is AcquisitionStage.SEMANTIC
        assert AcquisitionReason.of(ReasonCode.LLM_RERANK).stage is AcquisitionStage.ENRICHMENT

    def test_mechanical_and_model_derived_are_distinguishable(self) -> None:
        assert AcquisitionReason.of(ReasonCode.EXACT_IDENTIFIER_MATCH).is_mechanical
        assert AcquisitionReason.of(ReasonCode.RECENT_ACTIVITY).is_mechanical
        assert not AcquisitionReason.of(ReasonCode.VECTOR_RANK).is_mechanical
        assert not AcquisitionReason.of(ReasonCode.LLM_RERANK).is_mechanical

    def test_unknown_code_requires_an_explicit_stage(self) -> None:
        with pytest.raises(ValueError, match="stage"):
            AcquisitionReason.of("some_future_signal")

    def test_unknown_code_is_accepted_with_a_stage(self) -> None:
        reason = AcquisitionReason.of("ci_build_failed", stage=AcquisitionStage.EXTERNAL_STATE)
        assert reason.is_mechanical

    def test_detail_is_free_form_but_optional(self) -> None:
        reason = AcquisitionReason.of(ReasonCode.FTS_RANK, detail="rank 2")
        assert reason.detail == "rank 2"
        assert AcquisitionReason.of(ReasonCode.FTS_RANK).detail == ""

    def test_renders_for_debug_output(self) -> None:
        assert str(AcquisitionReason.of(ReasonCode.FTS_RANK, detail="rank 2")) == "fts_rank(rank 2)"
        assert str(AcquisitionReason.of(ReasonCode.FTS_RANK)) == "fts_rank"


class TestRetrievalCandidate:
    def _candidate(self, **overrides: object) -> RetrievalCandidate:
        defaults: dict[str, object] = {
            "concept_id": ConceptId.parse("0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31"),
            "retriever": "text",
            "rank": 1,
            "reason": AcquisitionReason.of(ReasonCode.FTS_RANK),
            "source_path": VaultPath.parse("30_Knowledge/Notes/thing.md"),
        }
        defaults.update(overrides)
        return RetrievalCandidate(**defaults)  # type: ignore[arg-type]

    def test_rank_is_one_based(self) -> None:
        with pytest.raises(ValueError, match="rank"):
            self._candidate(rank=0)

    def test_provider_score_is_optional(self) -> None:
        # A graph-expanded candidate has no meaningful score.
        assert self._candidate(retriever="graph", provider_score=None).provider_score is None

    def test_candidates_are_not_orderable_by_score(self) -> None:
        # core/06 section 6: scores from different engines are not comparable,
        # so the domain model refuses to give them a total ordering.
        low = self._candidate(retriever="text", provider_score=0.2)
        high = self._candidate(retriever="vector", provider_score=0.9, rank=2)
        with pytest.raises(TypeError):
            sorted([low, high])  # type: ignore[type-var]

    def test_candidate_id_is_deterministic_and_retriever_namespaced(self) -> None:
        assert self._candidate().candidate_id == self._candidate().candidate_id
        assert self._candidate().candidate_id != self._candidate(retriever="vector").candidate_id

    def test_metadata_is_read_only(self) -> None:
        candidate = self._candidate(metadata={"lifecycle": "current"})
        assert candidate.metadata["lifecycle"] == "current"
        with pytest.raises(TypeError):
            candidate.metadata["lifecycle"] = "stale"  # type: ignore[index]

    def test_requires_a_reason(self) -> None:
        with pytest.raises(TypeError):
            RetrievalCandidate(  # type: ignore[call-arg]
                concept_id=ConceptId.new(), retriever="text", rank=1
            )


class TestCapabilities:
    def test_capability_sets_are_declared_not_named(self) -> None:
        declared = frozenset({TextIndexCapability.EXACT_IDENTIFIERS})
        require_capability("FakeTextIndex", declared, TextIndexCapability.EXACT_IDENTIFIERS)

    def test_missing_capability_fails_loudly(self) -> None:
        declared: frozenset[VectorIndexCapability] = frozenset()
        with pytest.raises(CapabilityNotSupportedError, match="sparse_vectors"):
            require_capability(
                "DisabledVectorIndex", declared, VectorIndexCapability.SPARSE_VECTORS
            )

    def test_graph_and_vector_capabilities_stay_separate_vocabularies(self) -> None:
        # core/06 section 15: a graph vendor that also does vectors must not
        # silently become the vector vendor.
        assert set(GraphIndexCapability) & set(VectorIndexCapability) == set()
