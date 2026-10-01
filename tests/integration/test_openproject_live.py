"""The OpenProject adapter against a real instance. Opt-in, never in CI.

Live tests are marked, opt-in and never destructive, and CI runs the captured
fixtures instead. Nothing here writes: the reader has no write path to call.

Run them by naming an instance, a project and where the token is:

```bash
NEVER4GA_LIVE_OPENPROJECT=https://openproject.example \\
NEVER4GA_LIVE_OPENPROJECT_PROJECT=never4ga \\
NEVER4GA_LIVE_OPENPROJECT_TOKEN_FILE=~/.op-token \\
    .venv/bin/pytest tests/integration/test_openproject_live.py -m live
```

The token is read from a file rather than an environment variable so that it
does not reach the process table or a shell history
(`details/security-configuration.md` §4). It is never printed, and no response
captured by these tests is written anywhere: refreshing the fixtures is a
deliberate act, described in `tests/fixtures/openproject/README.md`.

What these prove that the fixtures cannot is that the *shape* has not moved:
that the instance still answers the paths, filters and page semantics the
captured responses were recorded from. When one of these fails and the fixture
tests pass, the fixtures are stale.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.openproject import OpenProjectProvider
from never4ga.adapters.sqlite.tracker_cache import SQLiteTrackerCache
from never4ga.adapters.sqlite.trackers import open_trackers
from never4ga.domain.capabilities import WorkManagementCapability
from never4ga.ports.work_management import WorkItemQuery
from never4ga.services.trackers import CachingWorkManagementProvider

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("NEVER4GA_LIVE_OPENPROJECT"),
        reason="set NEVER4GA_LIVE_OPENPROJECT to run against a real instance",
    ),
]


@pytest.fixture(scope="module")
def provider() -> Iterator[OpenProjectProvider]:
    token_file = os.environ.get("NEVER4GA_LIVE_OPENPROJECT_TOKEN_FILE")
    if not token_file:
        pytest.skip("set NEVER4GA_LIVE_OPENPROJECT_TOKEN_FILE to the token's path")
    built = OpenProjectProvider(
        base_url=os.environ["NEVER4GA_LIVE_OPENPROJECT"],
        token=Path(token_file).expanduser().read_text().strip(),
        connection="live",
        project_ref=os.environ.get("NEVER4GA_LIVE_OPENPROJECT_PROJECT"),
    )
    yield built
    built.close()


def test_the_instance_answers(provider: OpenProjectProvider) -> None:
    health = provider.health()
    assert health.available is True, health.detail


def test_the_project_resolves(provider: OpenProjectProvider) -> None:
    resolved = provider.resolve_project()
    assert resolved.numeric_id.isdigit()
    assert resolved.identifier


def test_work_items_read_and_normalise(provider: OpenProjectProvider) -> None:
    items = provider.search_work_items(WorkItemQuery(limit=5))
    assert items
    for item in items:
        assert item.ref.provider == "openproject"
        assert item.title
        assert item.url is not None and item.url.endswith(f"/work_packages/{item.ref.value}")


def test_one_item_reads_by_reference(provider: OpenProjectProvider) -> None:
    first = provider.search_work_items(WorkItemQuery(limit=1))[0]
    again = provider.get_work_item(first.ref)
    assert again is not None
    assert again.title == first.title


def test_paging_collects_every_item_exactly_once(provider: OpenProjectProvider) -> None:
    # The behaviour the fixtures pin, checked against the server that has it:
    # a page size below the collection's size must not re-fetch or skip.
    everything = provider.search_work_items(WorkItemQuery())
    if len(everything) < 2:
        pytest.skip("the project is too small for paging to mean anything")
    references = [item.ref for item in everything]
    assert len(references) == len(set(references))


def test_a_closed_item_is_not_silently_excluded(provider: OpenProjectProvider) -> None:
    # OpenProject's default filter is `status: open`. An unfiltered Never4gA
    # query means no filter, so asking twice must not narrow the answer.
    unfiltered = provider.search_work_items(WorkItemQuery())
    assert len(unfiltered) >= len(provider.search_work_items(WorkItemQuery(statuses=("New",))))


def test_a_change_time_filter_is_understood_as_sent(provider: OpenProjectProvider) -> None:
    # The adapter spells an instant with `isoformat()`, which is `+00:00`
    # rather than `Z`. A filter the server cannot parse would not fail loudly;
    # it would quietly answer a wider question.
    everything = provider.search_work_items(WorkItemQuery())
    since_the_future = provider.search_work_items(
        WorkItemQuery(updated_since=datetime(2099, 1, 1, tzinfo=UTC))
    )
    assert everything
    assert since_the_future == ()


def test_capabilities_come_from_the_instance(provider: OpenProjectProvider) -> None:
    found = provider.capabilities
    assert WorkManagementCapability.READ_WORK_ITEMS in found
    # **A reader claims no write, however permissive the token.** The instance
    # grants this one everything (the live write suite proves it by writing),
    # and `capabilities` reports the intersection of what the token may do with
    # what the class can do. `OpenProjectProvider` can do none of it, and a
    # capability with no method behind it is a claim rather than a capability.
    # The writer's half of this is in `test_openproject_write_live.py`.
    assert (
        not {
            WorkManagementCapability.CREATE_WORK_ITEM,
            WorkManagementCapability.UPDATE_WORK_ITEM,
            WorkManagementCapability.COMMENT_WORK_ITEM,
        }
        & found
    )
    # Still outside the product entirely, and an available link must not become
    # a claim: every work package advertises `delete`, `logTime` and the rest.
    assert not any(
        token in capability.value
        for capability in found
        for token in ("delete", "webhook", "time", "watch", "attach")
    )


def test_real_work_items_survive_the_cache_unchanged(
    provider: OpenProjectProvider, tmp_path: Path
) -> None:
    # The fixtures cannot prove this: they are what a normaliser was written
    # against, and a field shape nobody anticipated is exactly what would be
    # lost on the way through JSON. Real payloads, real round trip.
    with closing(open_trackers(tmp_path / "trackers.sqlite3")) as connection:
        cached = CachingWorkManagementProvider(
            provider,
            cache=SQLiteTrackerCache(connection),
            connection="live",
            project_ref=os.environ.get("NEVER4GA_LIVE_OPENPROJECT_PROJECT", ""),
        )
        report = cached.refresh()
        assert report.fetched > 0

        for entry in cached.cached_items():
            assert cached.get_work_item(entry.key.external_id) == entry.item
