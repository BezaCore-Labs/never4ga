"""Run the TrackerCache contract against every implementation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from never4ga.adapters.fakes import InMemoryTrackerCache
from never4ga.adapters.sqlite.tracker_cache import SQLiteTrackerCache
from never4ga.adapters.sqlite.trackers import open_trackers
from never4ga.ports.tracker_cache import TrackerCache
from tests.contracts.tracker_cache_contract import TrackerCacheContract

pytestmark = pytest.mark.contract


class TestInMemoryTrackerCache(TrackerCacheContract):
    @pytest.fixture
    def cache(self) -> TrackerCache:
        return InMemoryTrackerCache()


class TestSQLiteTrackerCache(TrackerCacheContract):
    @pytest.fixture
    def cache(self, tmp_path: Path) -> Iterator[TrackerCache]:
        with closing(open_trackers(tmp_path / "trackers.sqlite3")) as connection:
            yield SQLiteTrackerCache(connection)

    def test_it_survives_being_reopened(self, tmp_path: Path) -> None:
        # A cache that only remembers within one process is not a cache.
        from tests.contracts.tracker_cache_contract import entry, key

        database = tmp_path / "reopened.sqlite3"
        with closing(open_trackers(database)) as connection:
            SQLiteTrackerCache(connection).put(entry())
        with closing(open_trackers(database)) as connection:
            assert SQLiteTrackerCache(connection).get(key("838")) is not None
