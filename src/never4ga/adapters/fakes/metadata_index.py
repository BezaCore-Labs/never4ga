"""In-memory MetadataIndex."""

from __future__ import annotations

from collections.abc import Sequence

from never4ga.domain.capabilities import MetadataIndexCapability
from never4ga.domain.identity import ConceptId
from never4ga.ports.metadata_index import MetadataQuery, MetadataRecord

__all__ = ["InMemoryMetadataIndex"]


class InMemoryMetadataIndex:
    """A MetadataIndex backed by a dictionary, evaluating filters in Python."""

    def __init__(self) -> None:
        self._records: dict[ConceptId, MetadataRecord] = {}

    @property
    def capabilities(self) -> frozenset[MetadataIndexCapability]:
        return frozenset({MetadataIndexCapability.SET_FILTERS})

    def upsert(self, record: MetadataRecord) -> None:
        self._records[record.concept_id] = record

    def remove(self, concept_id: ConceptId) -> None:
        self._records.pop(concept_id, None)

    def get(self, concept_id: ConceptId) -> MetadataRecord | None:
        return self._records.get(concept_id)

    def query(self, query: MetadataQuery) -> Sequence[MetadataRecord]:
        matches = [record for record in self._records.values() if _matches(record, query)]
        # Deterministic ordering: identity is time-ordered, so this is insertion
        # order in practice and stable across rebuilds.
        matches.sort(key=lambda record: record.concept_id)
        if query.limit is not None:
            matches = matches[: query.limit]
        return tuple(matches)

    def clear(self) -> None:
        self._records.clear()


def _matches(record: MetadataRecord, query: MetadataQuery) -> bool:
    scalar_filters = (
        (query.types, record.concept_type),
        (query.lifecycles, record.lifecycle),
        (query.statuses, record.status),
        (query.authorities, record.authority),
    )
    for allowed, value in scalar_filters:
        if allowed and value not in allowed:
            return False
    if query.workspace_ids and record.workspace_id not in query.workspace_ids:
        return False
    set_filters = ((query.tags, record.tags), (query.domains, record.domains))
    return all(not wanted or set(wanted) & set(held) for wanted, held in set_filters)
