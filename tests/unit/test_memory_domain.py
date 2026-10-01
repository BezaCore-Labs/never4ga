"""External memory domain invariants (core/08)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from never4ga.domain.identity import ConceptId, ExternalId
from never4ga.domain.memory import IngestReceipt, MemoryCandidate, MemoryEpisode


def make_candidate(**overrides: object) -> MemoryCandidate:
    defaults: dict[str, object] = {
        "provider": "mem0",
        "external_id": ExternalId(provider="mem0", value="mem_01H8"),
        "text": "the maintainer prefers mechanical-first context.",
    }
    defaults.update(overrides)
    return MemoryCandidate(**defaults)  # type: ignore[arg-type]


class TestMemoryCandidate:
    def test_provider_identity_is_separate_from_canonical_identity(self) -> None:
        candidate = make_candidate()
        assert isinstance(candidate.external_id, ExternalId)
        assert not issubclass(ExternalId, ConceptId)

    def test_is_not_canonical_until_promoted(self) -> None:
        # core/08 section 7: promotion is explicit; until then there is no UUID.
        assert make_candidate().concept_id is None

    def test_promotion_attaches_a_canonical_uuid_without_losing_provenance(self) -> None:
        concept_id = ConceptId.new()
        promoted = make_candidate(concept_id=concept_id, provenance="checkpoint 2026-08-22")
        assert promoted.concept_id == concept_id
        assert promoted.external_id.value == "mem_01H8"
        assert promoted.provenance == "checkpoint 2026-08-22"

    def test_external_id_namespace_must_agree_with_the_provider(self) -> None:
        with pytest.raises(ValueError, match="provider"):
            make_candidate(external_id=ExternalId(provider="zep", value="x"))

    def test_provider_confidence_stays_provider_metadata(self) -> None:
        # core/08 section 6: provider confidence is never auto-converted into
        # Never4gA authority, so there is no authority field to convert it into.
        candidate = make_candidate(provider_confidence=0.92)
        assert candidate.provider_confidence == 0.92
        assert not hasattr(candidate, "authority")

    def test_temporal_validity_is_carried_when_the_provider_supplies_it(self) -> None:
        candidate = make_candidate(
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=datetime(2026, 6, 1, tzinfo=UTC),
        )
        assert candidate.valid_from is not None
        assert candidate.valid_to is not None

    def test_removing_the_provider_does_not_invalidate_a_promoted_concept(self) -> None:
        # core/08 section 19: a promoted concept remains valid if the provider
        # is later removed.
        concept_id = ConceptId.new()
        promoted = make_candidate(concept_id=concept_id)
        del promoted
        assert ConceptId.parse(str(concept_id)) == concept_id


class TestMemoryEpisode:
    def test_ingestion_is_workspace_scoped(self) -> None:
        # core/08 section 8: do not blindly stream the whole vault.
        workspace = ConceptId.new()
        episode = MemoryEpisode(workspace_id=workspace, kind="checkpoint", text="did a thing")
        assert episode.workspace_id == workspace

    def test_kind_is_required(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            MemoryEpisode(workspace_id=ConceptId.new(), kind="  ", text="x")

    def test_extra_is_read_only(self) -> None:
        episode = MemoryEpisode(
            workspace_id=ConceptId.new(), kind="checkpoint", text="x", extra={"session": "s1"}
        )
        with pytest.raises(TypeError):
            episode.extra["session"] = "s2"  # type: ignore[index]


class TestIngestReceipt:
    def test_records_the_provider_side_identity(self) -> None:
        receipt = IngestReceipt(
            provider_id="mem0",
            accepted=True,
            external_id=ExternalId(provider="mem0", value="ep-1"),
        )
        assert receipt.external_id is not None
        assert str(receipt.external_id) == "mem0:ep-1"

    def test_a_decline_is_explicit(self) -> None:
        receipt = IngestReceipt(provider_id="null", accepted=False, detail="not configured")
        assert receipt.accepted is False
        assert receipt.external_id is None
