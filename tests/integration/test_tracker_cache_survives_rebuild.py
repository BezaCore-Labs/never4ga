"""The tracker cache is a second database, and `rebuild` never touches it.

The reasoning is worth keeping next to the test. `rebuild` means "discard the
projection and build it again from Markdown". A cache of somebody else's
operational state cannot be rebuilt from Markdown at all, so if it shared the
index's file, `rebuild` would have to learn which tables to spare -- and the
day someone forgets, an offline machine loses its whole view of open work with
no way to get it back but a network it does not have.

These run through `main()`, because the composition root is where a mistake
like that would actually be made.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.sqlite.tracker_cache import SQLiteTrackerCache
from never4ga.adapters.sqlite.trackers import open_trackers
from never4ga.cli import main
from never4ga.domain.identity import ConceptId, ExternalId, WorkItemKey
from never4ga.platform_paths import PlatformPaths
from never4ga.ports.tracker_cache import CachedWorkItem
from never4ga.ports.work_management import WorkItem

Run = Callable[..., int]

CONNECTION = "work_openproject"
PROJECT = "never4ga"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str) -> int:
        code = main(["--vault", str(vault), *arguments])
        capsys.readouterr()
        return code

    return invoke


def vault_id(vault: Path) -> ConceptId:
    from never4ga.adapters.filesystem import FileSystemMarkdownStore
    from never4ga.layout import SYSTEM_MANIFEST

    manifest = FileSystemMarkdownStore(vault).get_by_path(SYSTEM_MANIFEST)
    assert manifest is not None
    return manifest.concept_id


def note(vault: Path, name: str) -> None:
    path = vault / "30_Knowledge" / "Notes" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "type": "knowledge",
        "id": str(ConceptId.new()),
        "schema": "never4ga/0.1",
        "title": name.replace("-", " ").title(),
        "created_at": "2026-08-25T12:00:00Z",
    }
    body = "\n".join(f"{key}: {json.dumps(value)}" for key, value in frontmatter.items())
    path.write_text(f"---\n{body}\n---\n\n# {frontmatter['title']}\n\nA note.\n", encoding="utf-8")


def cache_a_ticket(paths: PlatformPaths, identity: ConceptId) -> Path:
    database = paths.trackers_database(identity)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(open_trackers(database)) as connection:
        SQLiteTrackerCache(connection).put(
            CachedWorkItem(
                key=WorkItemKey(
                    connection=CONNECTION,
                    project_ref=PROJECT,
                    external_id=ExternalId(provider="openproject", value="838"),
                ),
                item=WorkItem(
                    ref=ExternalId(provider="openproject", value="838"),
                    title="Milestone 6 — OpenProject Read Integration",
                    status="In progress",
                ),
                fetched_at=datetime(2026, 8, 25, 18, 0, tzinfo=UTC),
            )
        )
    return database


def cached_titles(database: Path) -> list[str]:
    with closing(open_trackers(database)) as connection:
        entries = SQLiteTrackerCache(connection).entries(CONNECTION, PROJECT)
    return [entry.item.title for entry in entries]


class TestTheCacheIsItsOwnDatabase:
    def test_it_sits_beside_the_index_not_inside_it(self, run: Run, vault: Path) -> None:
        run("init")
        paths = PlatformPaths.resolve()
        identity = vault_id(vault)
        assert paths.trackers_database(identity).name == "trackers.sqlite3"
        assert paths.trackers_database(identity).parent == paths.index_database(identity).parent
        assert paths.trackers_database(identity) != paths.index_database(identity)

    def test_it_is_outside_the_git_backed_vault(self, run: Run, vault: Path) -> None:
        # core/05 section 7: derived state never lives in the vault.
        run("init")
        assert vault not in PlatformPaths.resolve().trackers_database(vault_id(vault)).parents


class TestRebuildLeavesItAlone:
    @pytest.fixture
    def cached(self, run: Run, vault: Path) -> Path:
        run("init")
        note(vault, "mechanical-context")
        run("index")
        return cache_a_ticket(PlatformPaths.resolve(), vault_id(vault))

    def test_a_rebuild_does_not_empty_the_cache(self, run: Run, cached: Path) -> None:
        assert cached_titles(cached) == ["Milestone 6 — OpenProject Read Integration"]
        assert run("rebuild") == 0
        assert cached_titles(cached) == ["Milestone 6 — OpenProject Read Integration"]

    def test_a_rebuild_does_not_rewrite_the_file_at_all(self, run: Run, cached: Path) -> None:
        before = cached.read_bytes()
        run("rebuild")
        assert cached.read_bytes() == before

    def test_an_index_run_does_not_touch_it_either(self, run: Run, cached: Path) -> None:
        before = cached.read_bytes()
        run("index")
        assert cached.read_bytes() == before

    def test_deleting_the_index_costs_the_cache_nothing(self, cached: Path, vault: Path) -> None:
        # The other half of the full-rebuild invariant
        # (details/data-indexing-maintenance.md section 17): each database can
        # be thrown away without the other noticing.
        PlatformPaths.resolve().index_database(vault_id(vault)).unlink(missing_ok=True)
        assert cached_titles(cached) == ["Milestone 6 — OpenProject Read Integration"]
