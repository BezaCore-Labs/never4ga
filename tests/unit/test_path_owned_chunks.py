"""A chunk owned by a path rather than a concept.

A foreign note (`core/02` §3.3) has no `id`, so the text index cannot hold it
under a concept identity. It must still hold it, because notes that cannot be
searched are of little use. The chunk's owner is its vault-relative path, which
is derived and rebuildable and is **never** a concept identity. A rename is a
new document and the old one gone, which is what a path-keyed derived record
should do.
"""

from __future__ import annotations

import pytest

from never4ga.domain.chunk import ChunkIdentity
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.retrieval import RetrievalCandidate

PATH = VaultPath.parse("Projects/garden/raised-beds.md")
OTHER = VaultPath.parse("Reading/thinking-fast-and-slow.md")


def path_owned(path: VaultPath = PATH, ordinal: int = 0) -> ChunkIdentity:
    return ChunkIdentity(
        concept_id=None,
        source=path,
        heading_path=("Raised beds",),
        ordinal=ordinal,
        content_hash="abc123",
        policy_version="chunk/0.1",
    )


class TestChunkIdentity:
    def test_a_chunk_with_no_concept_needs_a_source(self) -> None:
        with pytest.raises(ValueError, match="source"):
            ChunkIdentity(
                concept_id=None,
                heading_path=(),
                ordinal=0,
                content_hash="abc123",
                policy_version="chunk/0.1",
            )

    def test_the_owner_is_the_path(self) -> None:
        assert path_owned().owner == f"path:{PATH}"

    def test_a_concept_chunk_is_owned_by_its_concept(self) -> None:
        concept = ConceptId.new()
        chunk = ChunkIdentity(
            concept_id=concept,
            heading_path=(),
            ordinal=0,
            content_hash="abc123",
            policy_version="chunk/0.1",
        )
        assert chunk.owner == str(concept)
        assert chunk.source is None

    def test_two_paths_with_the_same_content_are_two_chunks(self) -> None:
        assert path_owned(PATH).key != path_owned(OTHER).key

    def test_the_key_is_stable(self) -> None:
        assert path_owned().key == path_owned().key

    def test_a_path_chunk_never_collides_with_a_concept_chunk(self) -> None:
        concept = ChunkIdentity(
            concept_id=ConceptId.new(),
            heading_path=("Raised beds",),
            ordinal=0,
            content_hash="abc123",
            policy_version="chunk/0.1",
        )
        assert path_owned().key != concept.key


class TestRetrievalCandidate:
    def test_a_candidate_with_no_concept_needs_a_source_path(self) -> None:
        with pytest.raises(ValueError, match="source_path"):
            RetrievalCandidate(
                concept_id=None,
                retriever="text",
                rank=1,
                reason=AcquisitionReason.of(ReasonCode.FTS_RANK),
            )

    def test_its_identity_is_deterministic(self) -> None:
        def make() -> RetrievalCandidate:
            return RetrievalCandidate(
                concept_id=None,
                retriever="text",
                rank=1,
                reason=AcquisitionReason.of(ReasonCode.FTS_RANK),
                chunk=path_owned(),
                source_path=PATH,
            )

        assert make().candidate_id == make().candidate_id

    def test_two_paths_are_two_candidates(self) -> None:
        def make(path: VaultPath) -> RetrievalCandidate:
            return RetrievalCandidate(
                concept_id=None,
                retriever="text",
                rank=1,
                reason=AcquisitionReason.of(ReasonCode.FTS_RANK),
                chunk=path_owned(path),
                source_path=path,
            )

        assert make(PATH).candidate_id != make(OTHER).candidate_id
