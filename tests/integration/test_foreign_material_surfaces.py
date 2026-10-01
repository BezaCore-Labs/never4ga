"""A pile of foreign material, reached through every surface.

Real files, real SQLite. The CLI runs `main()` in-process, MCP runs its
toolbox in-process, and the API runs under a test client over the same kind of
vault. A foreign note comes back from `search` and a focused pack with a path,
an excerpt, the reason `foreign_material` and no `id`; `concept get` on its
path says it is not a concept and names `adopt`; and a startup pack does not
carry the pile, because nothing structural reaches it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from never4ga.adapters.filesystem import (
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
    GitRepositoryLocator,
    WorkspaceMappingFile,
)
from never4ga.api import create_app
from never4ga.cli import EXIT_FAILED, EXIT_OK, main
from never4ga.domain.identity import ConceptId
from never4ga.mcp.server import ToolFailureError
from never4ga.mcp.toolbox import Toolbox
from never4ga.platform_paths import PlatformPaths
from never4ga.services import SessionFactory, VaultInitializer, WorkspaceService
from tests.integration.conftest import CREDENTIAL

Run = Callable[..., "Result"]

RAISED_BEDS = "Projects/garden/raised-beds.md"


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out or self.err)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def seed_pile(root: Path) -> None:
    write(root, RAISED_BEDS, "# Raised beds\n\nCedar, not pine, for the zanzibar bays.\n")
    write(root, "Projects/README.md", "# Projects\n\nWhat I am building.\n")
    write(
        root,
        "Reading/fast-and-slow.md",
        "---\ntags: [books]\n---\n# Fast and slow\n\nSystem one is quick.\n",
    )
    write(root, ".obsidian/app.json", "{}")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    # Overrides the API conftest's vault: the same initialised vault, with a
    # pile in it before `init` ran, so the pile is registered.
    root = tmp_path / "vault"
    root.mkdir()
    seed_pile(root)
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Test Vault"
    )
    return root


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        code = main(["--vault", str(vault), *(["--json"] if as_json else []), *arguments])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def prepared(run: Run, repository: Path) -> Run:
    """Indexed, with a workspace mapped to a repository."""
    created = run("workspace", "create", "Garden", "--type", "product", as_json=True).json
    assert run("workspace", "map", str(created["id"]), "--repo", str(repository)).code == EXIT_OK
    assert run("index").code == EXIT_OK
    return run


@pytest.fixture
def toolbox(vault: Path) -> Toolbox:
    return Toolbox(vault, local=True)


class TestSearch:
    def test_a_foreign_note_is_a_result_with_no_id(self, prepared: Run) -> None:
        (result,) = prepared("search", "zanzibar", as_json=True).json["results"]
        assert result["id"] is None
        assert result["type"] is None
        assert result["path"] == RAISED_BEDS
        assert result["reason"] == "foreign_material"
        assert "Cedar, not pine" in result["excerpt"]
        assert result["title"] == "Raised beds"

    def test_the_human_output_says_where_it_is(self, prepared: Run) -> None:
        out = prepared("search", "zanzibar").out
        assert "Raised beds  (foreign_material)" in out
        assert RAISED_BEDS in out

    def test_mcp_answers_identically(self, prepared: Run, toolbox: Toolbox) -> None:
        cli = prepared("search", "zanzibar", as_json=True).json
        assert toolbox.search({"query": "zanzibar"}) == cli

    def test_a_structural_filter_leaves_the_pile_out(self, prepared: Run) -> None:
        payload = prepared("search", "zanzibar", "--type", "knowledge", as_json=True).json
        assert payload["results"] == []


class TestContext:
    def test_a_focused_pack_carries_it_with_no_id(self, prepared: Run, repository: Path) -> None:
        pack = prepared("context", "focus", "zanzibar", "--cwd", str(repository), as_json=True).json
        (item,) = [item for item in pack["items"] if item["path"] == RAISED_BEDS]
        assert item["id"] is None
        assert item["category"] == "retrieved"
        assert item["reason"]["code"] == "foreign_material"
        assert item["reason"]["stage"] == "lexical"
        assert "Cedar, not pine" in item["body"]

    def test_mcp_answers_identically(
        self, prepared: Run, toolbox: Toolbox, repository: Path
    ) -> None:
        cli = prepared("context", "focus", "zanzibar", "--cwd", str(repository), as_json=True).json
        mcp = dict(toolbox.context_focus({"terms": ["zanzibar"], "cwd": str(repository)}))
        del mcp["session_id"], cli["session_id"]
        assert mcp == cli

    def test_a_deep_pack_carries_it_too(self, prepared: Run, repository: Path) -> None:
        pack = prepared(
            "context",
            "focus",
            "zanzibar",
            "--cwd",
            str(repository),
            "--depth",
            "deep",
            as_json=True,
        ).json
        assert [item["path"] for item in pack["items"] if item["id"] is None] == [RAISED_BEDS]

    def test_a_startup_pack_does_not(self, prepared: Run, repository: Path) -> None:
        # Nothing structural reaches a pile.
        pack = prepared("context", "startup", "--cwd", str(repository), as_json=True).json
        assert [item for item in pack["items"] if item["id"] is None] == []
        assert not any(item["path"].startswith(("Projects/", "Reading/")) for item in pack["items"])


class TestNotAConcept:
    def test_concept_get_on_its_path_says_so_and_names_adopt(self, prepared: Run) -> None:
        result = prepared("concept", "get", RAISED_BEDS, as_json=True)
        assert result.code == EXIT_FAILED
        error = result.json["error"]
        assert error["code"] == "not_a_concept"
        assert error["details"]["path"] == RAISED_BEDS
        assert "adopt" in error["repair_hint"]

    def test_an_unknown_path_is_still_not_found(self, prepared: Run) -> None:
        result = prepared("concept", "get", "Projects/nothing-here.md", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.json["error"]["code"] != "not_a_concept"

    def test_mcp_refuses_the_same_way(self, prepared: Run, toolbox: Toolbox) -> None:
        with pytest.raises(ToolFailureError) as refused:
            toolbox.get_concept({"id": RAISED_BEDS})
        assert refused.value.error.code == "not_a_concept"


class TestDoctor:
    def test_an_untracked_foreign_note_is_not_called_invisible_to_search(
        self, prepared: Run
    ) -> None:
        findings = prepared("doctor", as_json=True).json["findings"]
        (finding,) = [f for f in findings if f.get("path") == RAISED_BEDS]
        assert finding["code"] == "untracked_document"
        assert "invisible" not in finding["message"]
        assert "search" in finding["message"]


class TestTheApi:
    def test_search_returns_it_with_a_null_id(self, indexed: TestClient) -> None:
        response = indexed.post("/v1/concepts/search", json={"query": "zanzibar"})
        assert response.status_code == 200
        (result,) = response.json()["results"]
        assert result["id"] is None
        assert result["type"] is None
        assert result["path"] == RAISED_BEDS
        assert result["reason"] == "foreign_material"

    def test_reading_its_path_as_a_concept_is_refused(self, indexed: TestClient) -> None:
        response = indexed.get(f"/v1/concepts/{RAISED_BEDS}")
        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "not_a_concept"
        assert "adopt" in error["repair_hint"]

    def test_a_malformed_id_is_still_refused_as_one(self, indexed: TestClient) -> None:
        assert indexed.get("/v1/concepts/not-a-uuid").status_code == 400

    def test_a_focused_pack_carries_it_with_a_null_id(
        self, prepared: Run, sessions: SessionFactory, vault_id: str, repository: Path
    ) -> None:
        # Resolution needs the mappings `prepared` wrote through the CLI.
        workspaces = WorkspaceService(
            WorkspaceMappingFile(
                PlatformPaths.resolve().workspaces_file(ConceptId.parse(vault_id))
            ),
            GitRepositoryLocator(),
        )
        app = create_app(
            sessions=sessions,
            credential=CREDENTIAL,
            vault_id=ConceptId.parse(vault_id),
            workspaces=workspaces,
        )
        with TestClient(app, headers={"authorization": f"Bearer {CREDENTIAL}"}) as client:
            client.post("/v1/index/reconcile", json={})
            response = client.post(
                "/v1/context/focus",
                json={"scope": {"cwd": str(repository)}, "terms": ["zanzibar"]},
            )
        assert response.status_code == 200, response.text
        (item,) = [item for item in response.json()["items"] if item["path"] == RAISED_BEDS]
        assert item["id"] is None
        assert item["reason"]["code"] == "foreign_material"


class TestAdoptingOutOfThePile:
    """Adopting out of the pile, through each surface that has the verb (MCP has none)."""

    def test_the_cli_moves_it_and_reports_both_paths(self, prepared: Run, vault: Path) -> None:
        result = prepared("adopt", RAISED_BEDS, "--type", "knowledge", as_json=True)
        assert result.code == EXIT_OK, result.err
        payload = result.json
        assert payload["path"] == "30_Knowledge/Notes/raised-beds.md"
        assert payload["moved_from"] == RAISED_BEDS
        assert "foreign material" in payload["placement"]
        assert not (vault / RAISED_BEDS).exists()

    def test_the_human_output_names_both_ends(self, prepared: Run) -> None:
        out = prepared("adopt", RAISED_BEDS, "--type", "knowledge").out
        assert f"from: {RAISED_BEDS}" in out
        assert "path: 30_Knowledge/Notes/raised-beds.md" in out

    def test_after_index_the_concept_is_found_and_the_path_record_is_gone(
        self, prepared: Run
    ) -> None:
        adopted = prepared("adopt", RAISED_BEDS, "--type", "knowledge", as_json=True).json
        assert prepared("index").code == EXIT_OK
        (result,) = prepared("search", "zanzibar", as_json=True).json["results"]
        assert result["id"] == adopted["id"]
        assert result["type"] == "knowledge"
        assert result["path"] == "30_Knowledge/Notes/raised-beds.md"
        assert result["reason"] != "foreign_material"

    def test_the_cli_names_a_folder_with_in(self, prepared: Run) -> None:
        payload = prepared(
            "adopt", RAISED_BEDS, "--in", "10_Workspaces/Garden/Decisions", as_json=True
        ).json
        assert payload["path"] == "10_Workspaces/Garden/Decisions/raised-beds.md"

    def test_without_a_type_the_cli_names_the_candidates(self, prepared: Run, vault: Path) -> None:
        result = prepared("adopt", RAISED_BEDS, as_json=True)
        assert result.code == EXIT_FAILED
        error = result.json["error"]
        assert error["code"] == "concept_not_adopted"
        assert error["details"]["candidates"] == ["knowledge", "map"]
        assert "--type knowledge|map" in error["repair_hint"]
        assert (vault / RAISED_BEDS).is_file()

    def test_an_occupied_destination_is_refused(self, prepared: Run, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/raised-beds.md", "# Taken\n")
        result = prepared("adopt", RAISED_BEDS, "--type", "knowledge", as_json=True)
        assert result.code == EXIT_FAILED
        assert "already exists" in result.json["error"]["message"]
        assert (vault / RAISED_BEDS).is_file()

    def test_the_api_moves_it_and_reports_both_paths(
        self, indexed: TestClient, vault: Path
    ) -> None:
        response = indexed.post(
            "/v1/concepts/adopt", json={"path": RAISED_BEDS, "type": "knowledge"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["path"] == "30_Knowledge/Notes/raised-beds.md"
        assert body["moved_from"] == RAISED_BEDS
        assert not (vault / RAISED_BEDS).exists()

    def test_the_api_takes_a_folder(self, indexed: TestClient) -> None:
        response = indexed.post(
            "/v1/concepts/adopt", json={"path": RAISED_BEDS, "in": "30_Knowledge/Maps"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["path"] == "30_Knowledge/Maps/raised-beds.md"

    def test_the_api_names_the_candidates_as_data(self, indexed: TestClient) -> None:
        response = indexed.post("/v1/concepts/adopt", json={"path": RAISED_BEDS})
        assert response.status_code == 422
        assert response.json()["error"]["details"]["candidates"] == ["knowledge", "map"]

    def test_the_cli_reports_what_it_set_aside(self, prepared: Run, vault: Path) -> None:
        # core/02 section 3.3: a writer's value that does not fit is kept, not refused.
        write(vault, "Projects/active.md", "---\nstatus: active\n---\n# Active\n")
        result = prepared("adopt", "Projects/active.md", "--type", "knowledge", as_json=True)
        assert result.code == EXIT_OK, result.err
        assert result.json["set_aside"] == ["status"]

    def test_the_human_output_names_what_was_set_aside(self, prepared: Run, vault: Path) -> None:
        write(vault, "Projects/active.md", "---\nstatus: active\n---\n# Active\n")
        out = prepared("adopt", "Projects/active.md", "--type", "knowledge").out
        assert "kept: status, under extensions.adopted" in out

    def test_nothing_set_aside_is_not_reported(self, prepared: Run) -> None:
        payload = prepared("adopt", RAISED_BEDS, "--type", "knowledge", as_json=True).json
        assert "set_aside" not in payload

    def test_the_api_reports_what_it_set_aside(self, indexed: TestClient, vault: Path) -> None:
        write(vault, "Projects/active.md", "---\nstatus: active\n---\n# Active\n")
        response = indexed.post(
            "/v1/concepts/adopt", json={"path": "Projects/active.md", "type": "knowledge"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["set_aside"] == ["status"]

    def test_an_in_place_adoption_reports_no_move(self, indexed: TestClient, vault: Path) -> None:
        write(vault, "30_Knowledge/Notes/soil.md", "# Soil\n")
        response = indexed.post("/v1/concepts/adopt", json={"path": "30_Knowledge/Notes/soil.md"})
        assert response.status_code == 200, response.text
        assert response.json()["moved_from"] is None
