"""MetadataIndex contract (core/06 sections 2, 5; core/07 Stage B)."""

from __future__ import annotations

import pytest

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.ports.metadata_index import MetadataIndex, MetadataQuery, MetadataRecord


def make_record(
    concept_id: ConceptId | None = None,
    *,
    concept_type: str = "knowledge",
    path: str = "30_Knowledge/Notes/thing.md",
    title: str = "Thing",
    workspace_id: ConceptId | None = None,
    tags: tuple[str, ...] = (),
    domains: tuple[str, ...] = (),
    lifecycle: str | None = None,
    authority: str | None = None,
    **extra: object,
) -> MetadataRecord:
    return MetadataRecord(
        concept_id=concept_id or ConceptId.new(),
        concept_type=concept_type,
        path=VaultPath.parse(path),
        title=title,
        workspace_id=workspace_id,
        tags=tags,
        domains=domains,
        lifecycle=lifecycle,
        authority=authority,
        extra=extra,
    )


class MetadataIndexContract:
    @pytest.fixture
    def index(self) -> MetadataIndex:
        raise NotImplementedError("supply a MetadataIndex fixture")

    def test_upsert_then_get(self, index: MetadataIndex) -> None:
        record = make_record()
        index.upsert(record)
        loaded = index.get(record.concept_id)
        assert loaded is not None
        assert loaded.title == "Thing"

    def test_upsert_replaces_rather_than_duplicates(self, index: MetadataIndex) -> None:
        record = make_record(title="First")
        index.upsert(record)
        index.upsert(make_record(concept_id=record.concept_id, title="Second"))
        loaded = index.get(record.concept_id)
        assert loaded is not None
        assert loaded.title == "Second"
        assert len(index.query(MetadataQuery())) == 1

    def test_get_missing_returns_none(self, index: MetadataIndex) -> None:
        assert index.get(ConceptId.new()) is None

    def test_query_by_type(self, index: MetadataIndex) -> None:
        knowledge = make_record(concept_type="knowledge")
        decision = make_record(concept_type="decision", path="30_Knowledge/Notes/d.md")
        index.upsert(knowledge)
        index.upsert(decision)
        found = index.query(MetadataQuery(types=("decision",)))
        assert [r.concept_id for r in found] == [decision.concept_id]

    def test_query_by_workspace(self, index: MetadataIndex) -> None:
        workspace = ConceptId.new()
        inside = make_record(workspace_id=workspace)
        outside = make_record(path="30_Knowledge/Notes/other.md")
        index.upsert(inside)
        index.upsert(outside)
        found = index.query(MetadataQuery(workspace_ids=(workspace,)))
        assert [r.concept_id for r in found] == [inside.concept_id]

    def test_tag_and_domain_filters_are_any_of(self, index: MetadataIndex) -> None:
        tagged = make_record(tags=("architecture", "python"))
        other = make_record(tags=("cooking",), path="30_Knowledge/Notes/other.md")
        index.upsert(tagged)
        index.upsert(other)
        found = index.query(MetadataQuery(tags=("python", "nothing")))
        assert [r.concept_id for r in found] == [tagged.concept_id]

    def test_filters_combine_as_and(self, index: MetadataIndex) -> None:
        workspace = ConceptId.new()
        match = make_record(concept_type="decision", workspace_id=workspace)
        wrong_type = make_record(
            concept_type="knowledge", workspace_id=workspace, path="30_Knowledge/Notes/k.md"
        )
        index.upsert(match)
        index.upsert(wrong_type)
        found = index.query(MetadataQuery(types=("decision",), workspace_ids=(workspace,)))
        assert [r.concept_id for r in found] == [match.concept_id]

    def test_lifecycle_and_authority_filters(self, index: MetadataIndex) -> None:
        current = make_record(lifecycle="current", authority="authoritative")
        superseded = make_record(lifecycle="superseded", path="30_Knowledge/Notes/s.md")
        index.upsert(current)
        index.upsert(superseded)
        assert [r.concept_id for r in index.query(MetadataQuery(lifecycles=("current",)))] == [
            current.concept_id
        ]
        assert [
            r.concept_id for r in index.query(MetadataQuery(authorities=("authoritative",)))
        ] == [current.concept_id]

    def test_limit_is_respected(self, index: MetadataIndex) -> None:
        for i in range(5):
            index.upsert(make_record(path=f"30_Knowledge/Notes/{i}.md"))
        assert len(index.query(MetadataQuery(limit=2))) == 2

    def test_empty_query_returns_everything(self, index: MetadataIndex) -> None:
        for i in range(3):
            index.upsert(make_record(path=f"30_Knowledge/Notes/{i}.md"))
        assert len(index.query(MetadataQuery())) == 3

    def test_remove(self, index: MetadataIndex) -> None:
        record = make_record()
        index.upsert(record)
        index.remove(record.concept_id)
        assert index.get(record.concept_id) is None

    def test_clear_makes_the_projection_disposable(self, index: MetadataIndex) -> None:
        # core/06 section 2: every projection must be disposable and rebuildable.
        index.upsert(make_record())
        index.clear()
        assert index.query(MetadataQuery()) == ()

    def test_records_carry_uuid_identity_not_backend_rows(self, index: MetadataIndex) -> None:
        record = make_record()
        index.upsert(record)
        (loaded,) = index.query(MetadataQuery())
        assert isinstance(loaded.concept_id, ConceptId)

    def test_unknown_metadata_is_preserved(self, index: MetadataIndex) -> None:
        record = make_record(x_future="value")
        index.upsert(record)
        loaded = index.get(record.concept_id)
        assert loaded is not None
        assert loaded.extra["x_future"] == "value"
