"""TrackerCache contract.

Specification:
- details/external-work-management-architecture.md -- Stage 2, read-through
  cache; the tracker stays authoritative.
- details/openproject-adapter.md section 9 -- what an entry records.
- core/03 section 17 -- connection + project_ref + external_id is the identity.
- core/06 section 22 -- one suite, every implementation.

The round-trip tests are the ones with teeth. A normalised work item carries
datetimes, dates and external references, and a backend that stores JSON will
hand back strings unless it is careful. A caller must not be able to tell
whether an item came from the cache or from the tracker, because the whole
point of a read-through cache is that it is invisible.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from never4ga.domain.identity import ExternalId, WorkItemKey
from never4ga.ports.tracker_cache import CachedWorkItem, TrackerCache
from never4ga.ports.work_management import WorkItem

CONNECTION = "work_openproject"
PROJECT = "never4ga"
FETCHED_AT = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)


def key(value: str, *, project_ref: str = PROJECT) -> WorkItemKey:
    return WorkItemKey(
        connection=CONNECTION,
        project_ref=project_ref,
        external_id=ExternalId(provider="openproject", value=value),
    )


def entry(value: str = "838", **overrides: object) -> CachedWorkItem:
    reference = key(value)
    item = WorkItem(
        ref=reference.external_id,
        title="Milestone 6 — OpenProject Read Integration",
        status="In progress",
        assignee="Ada Example",
        priority="High",
        milestone="v0.1",
        updated_at=datetime(2026, 8, 25, 17, 30, tzinfo=UTC),
        url="https://openproject.example/work_packages/838",
        relations=(ExternalId(provider="openproject", value="840"),),
        extra={
            "connection": CONNECTION,
            "project_ref": PROJECT,
            "display_id": value,
            "type": "Epic",
            "created_at": datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            "start_date": date(2026, 8, 25),
            "due_date": date(2026, 9, 15),
            "percent_complete": 40,
            "lock_version": 3,
            "parent": ExternalId(provider="openproject", value="117"),
            "relation_kinds": {"840": "relates"},
            "extensions": {"customField7": {"raw": "escalated"}},
        },
    )
    arguments: dict[str, object] = {"key": reference, "item": item, "fetched_at": FETCHED_AT}
    arguments.update(overrides)
    return CachedWorkItem(**arguments)  # type: ignore[arg-type]


def _with_extra(item: WorkItem, **extra: object) -> WorkItem:
    return replace(item, extra={**item.extra, **extra})


class TrackerCacheContract:
    @pytest.fixture
    def cache(self) -> TrackerCache:
        raise NotImplementedError("supply a TrackerCache fixture")

    def test_an_absent_key_is_none(self, cache: TrackerCache) -> None:
        assert cache.get(key("404")) is None

    def test_what_goes_in_comes_back_identical(self, cache: TrackerCache) -> None:
        stored = entry()
        cache.put(stored)
        assert cache.get(stored.key) == stored

    def test_types_survive_the_round_trip(self, cache: TrackerCache) -> None:
        cache.put(entry())
        found = cache.get(key("838"))
        assert found is not None
        assert isinstance(found.item.updated_at, datetime)
        assert isinstance(found.item.extra["start_date"], date)
        assert isinstance(found.item.extra["created_at"], datetime)
        assert isinstance(found.item.extra["parent"], ExternalId)
        assert found.item.relations == (ExternalId(provider="openproject", value="840"),)

    def test_extension_data_survives_unflattened(self, cache: TrackerCache) -> None:
        # core/02's rule for schema extensions is the same rule here: what is
        # not understood must survive a round trip unchanged.
        cache.put(entry())
        found = cache.get(key("838"))
        assert found is not None
        assert found.item.extra["extensions"] == {"customField7": {"raw": "escalated"}}

    def test_putting_again_replaces_rather_than_duplicates(self, cache: TrackerCache) -> None:
        cache.put(entry())
        later = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
        cache.put(entry(fetched_at=later))
        found = cache.get(key("838"))
        assert found is not None
        assert found.fetched_at == later
        assert len(cache.entries(CONNECTION, PROJECT)) == 1

    def test_the_same_id_through_two_connections_is_two_items(self, cache: TrackerCache) -> None:
        # core/03 section 17: the provider id alone identifies nothing.
        cache.put(entry())
        other = WorkItemKey(
            connection="another_tracker",
            project_ref=PROJECT,
            external_id=ExternalId(provider="openproject", value="838"),
        )
        assert cache.get(other) is None

    def test_the_same_id_in_two_projects_is_two_items(self, cache: TrackerCache) -> None:
        cache.put(entry())
        assert cache.get(key("838", project_ref="another-project")) is None

    def test_entries_are_scoped_to_one_project(self, cache: TrackerCache) -> None:
        cache.put(entry("838"))
        cache.put(entry("839"))
        assert [found.key.external_id.value for found in cache.entries(CONNECTION, PROJECT)] == [
            "838",
            "839",
        ]
        assert cache.entries(CONNECTION, "another-project") == ()

    def test_entries_are_ordered_the_same_way_twice(self, cache: TrackerCache) -> None:
        for value in ("841", "838", "840"):
            cache.put(entry(value))
        first = [found.key for found in cache.entries(CONNECTION, PROJECT)]
        assert first == [found.key for found in cache.entries(CONNECTION, PROJECT)]

    def test_forgetting_removes_one_entry(self, cache: TrackerCache) -> None:
        cache.put(entry("838"))
        cache.put(entry("839"))
        cache.forget(key("838"))
        assert cache.get(key("838")) is None
        assert cache.get(key("839")) is not None

    def test_forgetting_something_absent_is_not_an_error(self, cache: TrackerCache) -> None:
        cache.forget(key("404"))

    def test_clearing_drops_everything(self, cache: TrackerCache) -> None:
        cache.put(entry("838"))
        cache.put(entry("839"))
        cache.clear()
        assert cache.entries(CONNECTION, PROJECT) == ()

    def test_an_entry_reports_what_section_9_records(self, cache: TrackerCache) -> None:
        cache.put(entry())
        found = cache.get(key("838"))
        assert found is not None
        assert found.fetched_at == FETCHED_AT
        assert found.provider_updated_at == datetime(2026, 8, 25, 17, 30, tzinfo=UTC)
        assert found.provider_version == "3"

    def test_age_is_measured_from_the_fetch(self, cache: TrackerCache) -> None:
        cache.put(entry())
        found = cache.get(key("838"))
        assert found is not None
        assert found.age(FETCHED_AT.replace(hour=19)) == 3600.0

    def test_an_item_with_nothing_optional_still_round_trips(self, cache: TrackerCache) -> None:
        bare = CachedWorkItem(
            key=key("900"),
            item=WorkItem(ref=key("900").external_id, title="A ticket and nothing else"),
            fetched_at=FETCHED_AT,
        )
        cache.put(bare)
        assert cache.get(bare.key) == bare

    def test_put_many_stores_a_whole_page(self, cache: TrackerCache) -> None:
        cache.put_many([entry("838"), entry("839"), entry("840")])
        assert len(cache.entries(CONNECTION, PROJECT)) == 3

    def test_a_sequence_in_extension_data_keeps_its_shape(self, cache: TrackerCache) -> None:
        # Nothing normalised puts a tuple in `extra` today. A provider's
        # extension data is arbitrary, though, and "indistinguishable from a
        # fresh fetch" has to mean it whatever a provider sends.
        stored = entry("901", item=_with_extra(entry("901").item, sequences=("a", "b")))
        cache.put(stored)
        found = cache.get(key("901"))
        assert found is not None
        assert found.item.extra["sequences"] == ("a", "b")

    def test_put_many_of_nothing_is_harmless(self, cache: TrackerCache) -> None:
        cache.put_many([])
        assert cache.entries(CONNECTION, PROJECT) == ()
