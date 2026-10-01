"""Read-through caching over a WorkManagementProvider.

Specification:
- details/openproject-adapter.md section 9 -- on an explicit ticket read, use a
  fresh cache if policy permits and refresh otherwise.
- details/external-work-management-architecture.md -- Stage 2; the tracker stays
  authoritative.
- core/05 section 19 -- degradation is reported, never disguised.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from never4ga.adapters.fakes import FakeWorkManagementProvider, InMemoryTrackerCache
from never4ga.domain.identity import ExternalId, WorkItemKey
from never4ga.errors import ProviderUnavailableError
from never4ga.ports.tracker_cache import TrackerCache
from never4ga.ports.work_management import (
    ProviderHealth,
    WorkItem,
    WorkItemQuery,
    WorkManagementProvider,
)
from never4ga.services.trackers import CachingWorkManagementProvider

CONNECTION = "work_openproject"
PROJECT = "never4ga"
NOW = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)


def reference(value: str) -> ExternalId:
    return ExternalId(provider="fake-pm", value=value)


def key(value: str) -> WorkItemKey:
    return WorkItemKey(connection=CONNECTION, project_ref=PROJECT, external_id=reference(value))


def item(value: str = "838", title: str = "Milestone 6") -> WorkItem:
    return WorkItem(
        ref=reference(value),
        title=title,
        status="New",
        updated_at=datetime(2026, 8, 25, 17, 0, tzinfo=UTC),
        extra={"connection": CONNECTION, "project_ref": PROJECT},
    )


class CountingProvider:
    """A provider that records what it was asked, and can be switched off."""

    provider_id = "fake-pm"

    def __init__(self, items: Sequence[WorkItem] = (), *, available: bool = True) -> None:
        self._inner = FakeWorkManagementProvider(items, available=available)
        self.gets: list[str] = []
        self.searches: list[WorkItemQuery] = []

    @property
    def capabilities(self) -> frozenset[object]:
        return frozenset(self._inner.capabilities)

    def get_work_item(self, ref: ExternalId) -> WorkItem | None:
        self.gets.append(ref.value)
        return self._inner.get_work_item(ref)

    def search_work_items(self, query: WorkItemQuery) -> Sequence[WorkItem]:
        self.searches.append(query)
        return self._inner.search_work_items(query)

    def health(self) -> ProviderHealth:
        return self._inner.health()


def build(
    provider: CountingProvider,
    cache: TrackerCache | None = None,
    *,
    now: datetime = NOW,
    max_age: timedelta = timedelta(minutes=15),
) -> CachingWorkManagementProvider:
    return CachingWorkManagementProvider(
        provider,  # type: ignore[arg-type]
        cache=cache if cache is not None else InMemoryTrackerCache(),
        connection=CONNECTION,
        project_ref=PROJECT,
        max_age=max_age,
        clock=lambda: now,
    )


class TestReadThrough:
    def test_a_cold_read_asks_the_tracker(self) -> None:
        provider = CountingProvider([item()])
        assert build(provider).get_work_item(reference("838")) == item()
        assert provider.gets == ["838"]

    def test_a_warm_read_does_not(self) -> None:
        provider = CountingProvider([item()])
        cached = build(provider)
        cached.get_work_item(reference("838"))
        cached.get_work_item(reference("838"))
        assert provider.gets == ["838"]

    def test_a_stale_entry_is_refetched(self) -> None:
        # section 9: "use fresh cache if policy permits; otherwise refresh".
        provider = CountingProvider([item()])
        cache = InMemoryTrackerCache()
        build(provider, cache, now=NOW).get_work_item(reference("838"))
        build(provider, cache, now=NOW + timedelta(hours=1)).get_work_item(reference("838"))
        assert provider.gets == ["838", "838"]

    def test_refresh_ignores_a_fresh_entry(self) -> None:
        provider = CountingProvider([item()])
        cached = build(provider)
        cached.get_work_item(reference("838"))
        cached.get_work_item(reference("838"), refresh=True)
        assert provider.gets == ["838", "838"]

    def test_a_cached_item_is_indistinguishable_from_a_fetched_one(self) -> None:
        provider = CountingProvider([item()])
        cached = build(provider)
        assert cached.get_work_item(reference("838")) == cached.get_work_item(reference("838"))

    def test_an_item_the_tracker_does_not_have_is_not_cached_as_absent(self) -> None:
        # A miss is not a fact worth remembering: the ticket may be created a
        # minute later, and a cached "no" would outlast it.
        provider = CountingProvider([])
        cached = build(provider)
        assert cached.get_work_item(reference("404")) is None
        assert cached.get_work_item(reference("404")) is None
        assert provider.gets == ["404", "404"]

    def test_a_search_populates_the_cache(self) -> None:
        provider = CountingProvider([item("838"), item("839")])
        cache = InMemoryTrackerCache()
        build(provider, cache).search_work_items(WorkItemQuery())
        assert len(cache.entries(CONNECTION, PROJECT)) == 2

    def test_a_search_always_asks_the_tracker(self) -> None:
        # A query is not a key. Answering one from the cache would answer a
        # question about *now* with what was true at some earlier time.
        provider = CountingProvider([item()])
        cached = build(provider)
        cached.search_work_items(WorkItemQuery())
        cached.search_work_items(WorkItemQuery())
        assert len(provider.searches) == 2


class TestDegradation:
    def test_a_read_from_an_unreachable_tracker_raises(self) -> None:
        # core/05 section 19: an empty answer from a work tracker means "no
        # open work", and must never stand in for "could not ask".
        with pytest.raises(ProviderUnavailableError):
            build(CountingProvider([item()], available=False)).search_work_items(WorkItemQuery())

    def test_a_warm_entry_is_not_served_in_place_of_a_failed_read(self) -> None:
        provider = CountingProvider([item()])
        cache = InMemoryTrackerCache()
        build(provider, cache).get_work_item(reference("838"))

        offline = build(CountingProvider([], available=False), cache, now=NOW + timedelta(hours=1))
        with pytest.raises(ProviderUnavailableError):
            offline.get_work_item(reference("838"))

    def test_what_is_cached_can_still_be_read_deliberately(self) -> None:
        # Offline, the cache is still worth having -- but a caller has to ask
        # for it by name and gets `fetched_at` with it, so a stale answer can
        # never be mistaken for a live one.
        provider = CountingProvider([item()])
        cache = InMemoryTrackerCache()
        build(provider, cache).get_work_item(reference("838"))

        offline = build(CountingProvider([], available=False), cache)
        entries = offline.cached_items()
        assert [entry.item.title for entry in entries] == ["Milestone 6"]
        assert entries[0].fetched_at == NOW

    def test_health_still_answers_through_the_cache(self) -> None:
        assert build(CountingProvider([], available=False)).health().available is False


class TestRefresh:
    def test_refreshing_replaces_what_was_cached(self) -> None:
        cache = InMemoryTrackerCache()
        build(CountingProvider([item(title="Old")]), cache).refresh()
        build(CountingProvider([item(title="New")]), cache).refresh()
        assert [entry.item.title for entry in cache.entries(CONNECTION, PROJECT)] == ["New"]

    def test_refreshing_reports_what_it_stored(self) -> None:
        report = build(CountingProvider([item("838"), item("839")])).refresh()
        assert report.fetched == 2
        assert report.at == NOW

    def test_refreshing_forgets_what_the_tracker_no_longer_returns(self) -> None:
        # A ticket moved to another project would otherwise stay cached for
        # ever, and a derived view would keep showing work that is not there.
        cache = InMemoryTrackerCache()
        build(CountingProvider([item("838"), item("839")]), cache).refresh()
        report = build(CountingProvider([item("838")]), cache).refresh()
        assert [entry.key.external_id.value for entry in cache.entries(CONNECTION, PROJECT)] == [
            "838"
        ]
        assert report.forgotten == 1

    def test_a_search_prunes_too_because_it_is_the_path_production_takes(self) -> None:
        # Production reads go through `search_work_items`, not `refresh()`. A
        # search that only cached on the way past and never dropped anything
        # would let the cache grow forever.
        cache = InMemoryTrackerCache()
        build(CountingProvider([item("838"), item("839")]), cache).search_work_items(
            WorkItemQuery(limit=200)
        )
        assert len(cache.entries(CONNECTION, PROJECT)) == 2
        build(CountingProvider([item("838")]), cache).search_work_items(WorkItemQuery(limit=200))
        assert [entry.key.external_id.value for entry in cache.entries(CONNECTION, PROJECT)] == [
            "838"
        ]

    def test_a_narrowed_search_still_prunes_nothing(self) -> None:
        cache = InMemoryTrackerCache()
        build(CountingProvider([item("838"), item("839")]), cache).search_work_items(
            WorkItemQuery(limit=200)
        )
        build(CountingProvider([item("838")]), cache).search_work_items(
            WorkItemQuery(statuses=("New",))
        )
        assert len(cache.entries(CONNECTION, PROJECT)) == 2

    def test_a_capped_refresh_that_came_back_under_its_cap_still_forgets(self) -> None:
        # Every real caller passes a limit; the startup pack asks for at most
        # 200. Treating any cap as disqualifying would mean nothing is ever
        # forgotten. Asking for at most 200 and being handed one means the
        # tracker had no more to give.
        cache = InMemoryTrackerCache()
        build(CountingProvider([item("838"), item("839")]), cache).refresh(WorkItemQuery(limit=200))
        report = build(CountingProvider([item("838")]), cache).refresh(WorkItemQuery(limit=200))
        assert report.forgotten == 1
        assert [entry.key.external_id.value for entry in cache.entries(CONNECTION, PROJECT)] == [
            "838"
        ]

    def test_a_refresh_that_filled_its_cap_forgets_nothing(self) -> None:
        # A full answer is inconclusive: the tracker may have had more, and
        # whatever sat beyond the ceiling was never asked about.
        cache = InMemoryTrackerCache()
        build(CountingProvider([item("838"), item("839")]), cache).refresh()
        report = build(CountingProvider([item("838")]), cache).refresh(WorkItemQuery(limit=1))
        assert report.forgotten == 0
        assert len(cache.entries(CONNECTION, PROJECT)) == 2

    def test_a_failed_refresh_leaves_the_cache_alone(self) -> None:
        cache = InMemoryTrackerCache()
        build(CountingProvider([item()]), cache).refresh()
        with pytest.raises(ProviderUnavailableError):
            build(CountingProvider([], available=False), cache).refresh()
        assert len(cache.entries(CONNECTION, PROJECT)) == 1


class TestIdentity:
    def test_entries_are_keyed_by_all_three_terms(self) -> None:
        cache = InMemoryTrackerCache()
        build(CountingProvider([item()]), cache).get_work_item(reference("838"))
        assert cache.get(key("838")) is not None

    def test_it_satisfies_the_port_it_wraps(self) -> None:
        assert isinstance(build(CountingProvider([])), WorkManagementProvider)

    def test_it_carries_the_wrapped_provider_identity(self) -> None:
        cached = build(CountingProvider([]))
        assert cached.provider_id == "fake-pm"


class TestScope:
    def test_an_item_is_cached_under_the_project_it_came_from(self) -> None:
        # A search may name a project other than the one this instance is
        # configured for. Filing those results under the configured project
        # would make a cache that answers questions about the wrong tracker.
        elsewhere = WorkItem(
            ref=reference("900"),
            title="Someone else's ticket",
            extra={"connection": CONNECTION, "project_ref": "another-project"},
        )
        cache = InMemoryTrackerCache()
        build(CountingProvider([elsewhere]), cache).search_work_items(
            WorkItemQuery(project="another-project")
        )
        assert cache.entries(CONNECTION, PROJECT) == ()
        assert len(cache.entries(CONNECTION, "another-project")) == 1

    def test_everything_a_search_stores_shares_one_fetch_time(self) -> None:
        # One answer arrived at one moment. Reading the clock per item would
        # give the same response several different ages, and the last item in a
        # long page would look fresher than the first.
        ticks = iter(NOW + timedelta(seconds=offset) for offset in range(100))
        cache = InMemoryTrackerCache()
        CachingWorkManagementProvider(
            CountingProvider([item("838"), item("839")]),  # type: ignore[arg-type]
            cache=cache,
            connection=CONNECTION,
            project_ref=PROJECT,
            clock=lambda: next(ticks),
        ).search_work_items(WorkItemQuery())
        assert len({entry.fetched_at for entry in cache.entries(CONNECTION, PROJECT)}) == 1


class TestPruning:
    def test_a_narrowed_refresh_does_not_forget_what_it_did_not_ask_about(self) -> None:
        # Forgetting is for items the tracker no longer returns. A refresh
        # filtered to one status never asked about the others, and treating
        # "not in this answer" as "gone" would empty the cache a slice at a
        # time.
        cache = InMemoryTrackerCache()
        done = WorkItem(
            ref=reference("839"),
            title="Milestone 5",
            status="Closed",
            extra={"connection": CONNECTION, "project_ref": PROJECT},
        )
        provider = CountingProvider([item("838"), done])
        build(provider, cache).refresh()
        assert len(cache.entries(CONNECTION, PROJECT)) == 2

        report = build(provider, cache).refresh(WorkItemQuery(statuses=("New",)))
        assert report.forgotten == 0
        assert len(cache.entries(CONNECTION, PROJECT)) == 2

    def test_a_refresh_of_another_project_leaves_this_one_alone(self) -> None:
        cache = InMemoryTrackerCache()
        build(CountingProvider([item("838")]), cache).refresh()
        build(CountingProvider([]), cache).refresh(WorkItemQuery(project="another-project"))
        assert len(cache.entries(CONNECTION, PROJECT)) == 1

    def test_a_limited_refresh_does_not_forget_the_rest(self) -> None:
        cache = InMemoryTrackerCache()
        build(CountingProvider([item("838"), item("839")]), cache).refresh()
        build(CountingProvider([item("838")]), cache).refresh(WorkItemQuery(limit=1))
        assert len(cache.entries(CONNECTION, PROJECT)) == 2


class TestEntries:
    def test_a_fetch_time_without_a_timezone_is_refused(self) -> None:
        from never4ga.ports.tracker_cache import CachedWorkItem

        with pytest.raises(ValueError, match="timezone"):
            CachedWorkItem(key=key("838"), item=item(), fetched_at=datetime(2026, 8, 25, 18, 0))

    def test_it_is_usable_wherever_the_port_is_expected(self) -> None:
        # Checked by the type checker as much as at runtime: the decorator adds
        # a keyword to `get_work_item`, and that must stay compatible.
        wrapped: WorkManagementProvider = build(CountingProvider([item()]))
        assert wrapped.get_work_item(reference("838")) is not None
