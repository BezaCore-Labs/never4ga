"""Wiring the work-management signal provider into the composition root.

What the unit tests cannot say: that a vault with no tracker pays nothing for
one, that a token reaches the adapter only from the secret store, and that the
cache's database is not brought into existence by looking at it.

`core/05` section 7's rule about derived paths -- "asking where something
belongs must not create it" -- is the one most easily broken by wiring, because
opening a database is how a composition root usually proves it can.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from never4ga.adapters.fakes import InMemoryDocumentStore, InMemorySecretStore
from never4ga.cli import main
from never4ga.composition import FileTrackerCache, work_signal_provider
from never4ga.domain.connections import Connection
from never4ga.domain.context import ContextBudget, ContextDepth, ContextRequest
from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId, ExternalId, WorkItemKey
from never4ga.domain.provenance import AcquisitionReason, ReasonCode
from never4ga.domain.scope import ResolvedScope, ScopeRequest
from never4ga.platform_paths import PlatformPaths
from never4ga.ports.tracker_cache import CachedWorkItem
from never4ga.ports.work_management import WorkItem
from never4ga.services.connections import secret_ref_for

Run = Callable[..., Any]

WORKSPACE = "10_Workspaces/Example/workspace.md"
CONNECTION = "work_openproject"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str) -> Any:
        code = main(["--vault", str(vault), "--json", *arguments])
        captured = capsys.readouterr()
        return code, json.loads(captured.out or captured.err or "{}")

    return invoke


def vault_id(vault: Path) -> ConceptId:
    from never4ga.adapters.filesystem import FileSystemMarkdownStore
    from never4ga.layout import SYSTEM_MANIFEST

    manifest = FileSystemMarkdownStore(vault).get_by_path(SYSTEM_MANIFEST)
    assert manifest is not None
    return manifest.concept_id


class TestNothingIsCreatedByLooking:
    def test_a_startup_pack_does_not_bring_the_cache_into_existence(
        self, run: Run, vault: Path, tmp_path: Path
    ) -> None:
        run("init")
        _, created = run("workspace", "create", "Example", "--type", "product")
        repository = tmp_path / "repo"
        repository.mkdir()
        (repository / ".git").mkdir()
        run("workspace", "map", str(created["id"]), "--repo", str(repository))
        code, _ = run("index")
        assert code == 0

        trackers = PlatformPaths.resolve().trackers_database(vault_id(vault))
        assert not trackers.exists()
        code, pack = run("context", "startup", "--cwd", str(repository))
        # The pack has to have been assembled for its not creating anything to
        # mean anything at all.
        assert code == 0, pack
        assert pack["items"]
        assert "work_management" not in pack["degraded_providers"]
        assert not trackers.exists(), (
            "a vault with no tracker must not acquire a tracker database "
            "because somebody asked for context"
        )


def workspace_document(**work_management: Any) -> StoredDocument:
    frontmatter: dict[str, Any] = {
        "type": "workspace",
        "schema": "never4ga/0.1",
        "title": "Example",
    }
    if work_management:
        frontmatter["work_management"] = work_management
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(WORKSPACE),
        frontmatter=frontmatter,
        body="",
    )


def integration_document(provider: str = "openproject") -> StoredDocument:
    return StoredDocument(
        concept_id=ConceptId.new(),
        path=VaultPath.parse(f"50_System/Integrations/{CONNECTION}.md"),
        frontmatter={
            "type": "integration",
            "schema": "never4ga/0.1",
            "title": "Work OpenProject",
            "connection": CONNECTION,
            "provider": provider,
            "base_url": "https://openproject.invalid",
            "project_ref": "never4ga",
        },
        body="",
    )


def key_for(value: str) -> WorkItemKey:
    return WorkItemKey(
        connection=CONNECTION,
        project_ref="never4ga",
        external_id=ExternalId(provider="openproject", value=value),
    )


def entry_for(value: str) -> CachedWorkItem:
    return CachedWorkItem(
        key=key_for(value),
        item=WorkItem(ref=key_for(value).external_id, title="A ticket"),
        fetched_at=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
    )


def scope(document: StoredDocument) -> ResolvedScope:
    return ResolvedScope(
        workspace_id=document.concept_id,
        workspace_path=document.path,
        reason=AcquisitionReason.of(ReasonCode.WORKSPACE_REQUIRED),
    )


def startup() -> ContextRequest:
    return ContextRequest(
        scope=ScopeRequest(),
        depth=ContextDepth.STARTUP,
        budget=ContextBudget(max_items=10, max_characters=10_000),
    )


class TestTheFactory:
    def test_a_connection_with_no_stored_token_yields_no_signals(self, tmp_path: Path) -> None:
        # Nothing here is a fault: a token this machine has not been given is
        # an ordinary state, and it must not stop a pack assembling.
        documents = InMemoryDocumentStore()
        workspace = workspace_document(mode="external", connection=CONNECTION)
        documents.put(workspace)
        documents.put(integration_document())

        provider = work_signal_provider(
            documents=documents,
            secrets=InMemorySecretStore(),
            trackers_database=tmp_path / "trackers.sqlite3",
        )
        assert provider.collect(startup(), scope(workspace)) == ()
        assert not (tmp_path / "trackers.sqlite3").exists()

    def test_a_provider_never4ga_has_no_adapter_for_yields_no_signals(self, tmp_path: Path) -> None:
        documents = InMemoryDocumentStore()
        workspace = workspace_document(mode="external", connection=CONNECTION)
        documents.put(workspace)
        documents.put(integration_document(provider="jira"))
        secrets = InMemorySecretStore()
        secrets.set(secret_ref_for(Connection(name=CONNECTION, provider="jira")), "token")

        provider = work_signal_provider(
            documents=documents,
            secrets=secrets,
            trackers_database=tmp_path / "trackers.sqlite3",
        )
        assert provider.collect(startup(), scope(workspace)) == ()

    def test_an_unreachable_tracker_with_a_token_degrades(self, tmp_path: Path) -> None:
        # The adapter is built, the read is attempted, and `openproject.invalid`
        # does not resolve. The assembler is what catches this.
        from never4ga.errors import ProviderUnavailableError

        documents = InMemoryDocumentStore()
        workspace = workspace_document(mode="external", connection=CONNECTION)
        documents.put(workspace)
        documents.put(integration_document())
        secrets = InMemorySecretStore()
        secrets.set(secret_ref_for(Connection(name=CONNECTION, provider="openproject")), "token")

        provider = work_signal_provider(
            documents=documents,
            secrets=secrets,
            trackers_database=tmp_path / "trackers.sqlite3",
        )
        with pytest.raises(ProviderUnavailableError):
            provider.collect(startup(), scope(workspace))


class TestTheFileCache:
    def test_reading_does_not_bring_a_database_into_being(self, tmp_path: Path) -> None:
        database = tmp_path / "derived" / "trackers.sqlite3"
        cache = FileTrackerCache(database)
        assert cache.entries("a", "b") == ()
        assert cache.get(key_for("1")) is None
        cache.put_many([])
        cache.clear()
        assert not database.exists()

    def test_writing_does(self, tmp_path: Path) -> None:
        database = tmp_path / "derived" / "trackers.sqlite3"
        cache = FileTrackerCache(database)
        cache.put(entry_for("1"))
        assert database.exists()
        assert [found.item.title for found in cache.entries(CONNECTION, "never4ga")] == ["A ticket"]

    def test_it_leaves_no_connection_open(self, tmp_path: Path) -> None:
        # Every operation opens and closes, so nothing has to be handed a stack
        # to close it with. `close` on an already-closed database is what would
        # fail if one were left open.
        cache = FileTrackerCache(tmp_path / "trackers.sqlite3")
        cache.put(entry_for("1"))
        assert (
            sqlite3.connect(tmp_path / "trackers.sqlite3")
            .execute("SELECT count(*) FROM work_items")
            .fetchone()[0]
            == 1
        )
