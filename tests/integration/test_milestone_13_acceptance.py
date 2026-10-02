"""A pile of existing notes becomes a vault, end to end.

The pile has notes with and without frontmatter, nested folders, a
`README.md` and a `.obsidian/` directory. The acceptance criteria run in order
through the CLI, and through the API and MCP wherever each has the verb. Each
step has its focused tests elsewhere; this is the one place the steps run in
sequence over one vault, so a change that breaks the hand-off between two of
them fails here even when both halves still pass alone.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from never4ga.api import create_app
from never4ga.cli import EXIT_FAILED, EXIT_OK, main
from never4ga.domain.identity import ConceptId
from never4ga.mcp.server import ToolFailureError
from never4ga.mcp.toolbox import Toolbox
from never4ga.services import SessionFactory
from tests.integration.conftest import CREDENTIAL

RAISED_BEDS = "Projects/garden/raised-beds.md"
MEETING = "Projects/garden/meetings/2026-05-12.md"
BOOK = "Reading/thinking-fast-and-slow.md"

PILE = {
    RAISED_BEDS: (
        "---\ntags: [garden]\nsoil: loam\nstatus: active\n---\n# Raised beds\n\nZanzibar cedar.\n"
    ),
    MEETING: "# Tuesday sync\n\nBought the quillwort drip kit.\n",
    "Projects/README.md": "# Projects\n\nWhat I am building.\n",
    BOOK: "---\ntitle: Thinking Fast and Slow\nauthor: Kahneman\n---\nNotes on system one.\n",
    "README.md": "# My notes\n",
    ".obsidian/app.json": "{}",
}


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out or self.err)


Run = Callable[..., Result]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """The pile, before `init` has run over it.

    Overrides the API conftest's initialised vault: here `init` is the first
    thing under test, so the sessions and the client see the vault it made.
    """
    root = tmp_path / "vault"
    for relative, text in PILE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str) -> Result:
        code = main(["--vault", str(vault), "--json", *arguments])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def initialised(run: Run) -> Run:
    assert run("init").code == EXIT_OK
    return run


@pytest.fixture
def indexed(initialised: Run) -> Run:
    assert initialised("index").code == EXIT_OK
    return initialised


@pytest.fixture
def api(indexed: Run, sessions: SessionFactory, vault_id: str) -> Iterator[TestClient]:
    app = create_app(sessions=sessions, credential=CREDENTIAL, vault_id=ConceptId.parse(vault_id))
    with TestClient(app, headers={"authorization": f"Bearer {CREDENTIAL}"}) as client:
        client.post("/v1/index/reconcile", json={})
        yield client


@pytest.fixture
def mcp(indexed: Run, vault: Path) -> Toolbox:
    return Toolbox(vault, local=True)


def untouched(vault: Path) -> bool:
    return all(
        (vault / relative).read_text(encoding="utf-8") == text for relative, text in PILE.items()
    )


class TestCriterion1Init:
    def test_it_registers_each_directory_and_counts_its_files(self, run: Run) -> None:
        result = run("init")
        assert result.code == EXIT_OK
        assert result.json["foreign_material"] == [
            {"directory": "Projects", "files": 3},
            {"directory": "Reading", "files": 1},
        ]
        assert result.json["foreign_notes"] == ["README.md"]

    def test_preserved_counts_what_was_already_there(self, run: Run) -> None:
        preserved = run("init").json["preserved"]
        assert sorted(preserved) == sorted(p for p in PILE if not p.startswith("."))

    def test_nothing_that_was_there_is_written_to(self, run: Run, vault: Path) -> None:
        run("init")
        assert untouched(vault)

    def test_a_second_run_does_nothing(self, initialised: Run, vault: Path) -> None:
        manifest = (vault / "50_System/system.md").read_text(encoding="utf-8")
        again = initialised("init").json
        assert again["already_initialized"] is True
        assert again["created"] == []
        assert again["updated"] == []
        assert (vault / "50_System/system.md").read_text(encoding="utf-8") == manifest


class TestCriterion2Registration:
    def test_the_manifest_records_the_registration(self, initialised: Run) -> None:
        status = initialised("status").json
        assert status["foreign_material"] == ["Projects", "README.md", "Reading"]

    def test_a_directory_added_later_is_noticed_not_indexed(
        self, indexed: Run, vault: Path
    ) -> None:
        (vault / "Later").mkdir()
        (vault / "Later" / "quillwort.md").write_text("# Quillwort\n", encoding="utf-8")
        findings = indexed("doctor").json["findings"]
        assert {"code": "foreign_material_unregistered", "path": "Later"}.items() <= next(
            f for f in findings if f["code"] == "foreign_material_unregistered"
        ).items()
        assert indexed("index").code == EXIT_OK
        paths = [r["path"] for r in indexed("search", "quillwort").json["results"]]
        assert "Later/quillwort.md" not in paths


class TestCriterion3Doctor:
    def test_no_error_on_the_fresh_pile(self, initialised: Run) -> None:
        report = initialised("doctor").json
        assert report["healthy"] is True
        assert [f for f in report["findings"] if f["severity"] == "error"] == []

    def test_every_foreign_note_is_untracked_at_most(self, initialised: Run) -> None:
        findings = initialised("doctor").json["findings"]
        codes = {f["path"]: f["code"] for f in findings if f.get("path") in PILE}
        assert codes[RAISED_BEDS] == "untracked_document"
        assert codes[BOOK] == "untracked_document"
        assert "missing_id" not in codes.values()

    def test_the_api_and_mcp_agree(self, initialised: Run, api: TestClient, mcp: Toolbox) -> None:
        cli = initialised("doctor").json
        assert api.get("/v1/doctor").json()["findings"] == cli["findings"]
        assert mcp.doctor({})["findings"] == cli["findings"]


class TestCriterion4Search:
    def test_index_reports_the_foreign_notes(self, initialised: Run) -> None:
        assert initialised("index").json["foreign"]["indexed"] == 5

    def test_search_returns_one_with_a_path_an_excerpt_and_no_id(self, indexed: Run) -> None:
        (result,) = indexed("search", "quillwort").json["results"]
        assert result["path"] == MEETING
        assert result["id"] is None
        assert result["reason"] == "foreign_material"
        assert "quillwort" in result["excerpt"]

    def test_the_api_and_mcp_return_the_same(
        self, indexed: Run, api: TestClient, mcp: Toolbox
    ) -> None:
        cli = indexed("search", "quillwort").json
        assert mcp.search({"query": "quillwort"}) == cli
        (result,) = api.post("/v1/concepts/search", json={"query": "quillwort"}).json()["results"]
        assert (result["path"], result["id"]) == (MEETING, None)

    def test_concept_get_says_it_is_not_a_concept_and_names_adopt(
        self, indexed: Run, api: TestClient, mcp: Toolbox
    ) -> None:
        refused = indexed("concept", "get", MEETING)
        assert refused.code == EXIT_FAILED
        assert refused.json["error"]["code"] == "not_a_concept"
        assert "adopt" in refused.json["error"]["repair_hint"]
        assert api.get(f"/v1/concepts/{MEETING}").json()["error"]["code"] == "not_a_concept"
        with pytest.raises(ToolFailureError) as raised:
            mcp.get_concept({"id": MEETING})
        assert raised.value.error.code == "not_a_concept"


class TestCriterion5Context:
    """A pack is scoped to a workspace, so one is created and mapped first.

    A pile with no workspace has no scope to assemble a pack for: `context`
    refuses `scope_unresolved` until a repository is mapped. Search is what
    reaches the pile on the first run; a pack reaches it once there is a
    workspace to be focused in.
    """

    @pytest.fixture
    def mapped(self, indexed: Run, tmp_path: Path) -> Path:
        repository = tmp_path / "repo"
        (repository / ".git").mkdir(parents=True)
        created = indexed("workspace", "create", "Garden", "--type", "product").json
        assert indexed("workspace", "map", created["id"], "--repo", str(repository)).code == 0
        assert indexed("index").code == EXIT_OK
        return repository

    def test_a_focused_pack_carries_it_as_retrieved_with_no_id(
        self, indexed: Run, mapped: Path
    ) -> None:
        pack = indexed("context", "focus", "quillwort", "--cwd", str(mapped)).json
        (item,) = [item for item in pack["items"] if item["path"] == MEETING]
        assert item["id"] is None
        assert item["category"] == "retrieved"
        assert item["reason"]["code"] == "foreign_material"

    def test_the_mcp_pack_is_the_same(self, indexed: Run, mapped: Path, mcp: Toolbox) -> None:
        cli = indexed("context", "focus", "quillwort", "--cwd", str(mapped)).json
        pack = dict(mcp.context_focus({"terms": ["quillwort"], "cwd": str(mapped)}))
        del pack["session_id"], cli["session_id"]
        assert pack == cli

    def test_a_startup_pack_does_not(self, indexed: Run, mapped: Path) -> None:
        pack = indexed("context", "startup", "--cwd", str(mapped)).json
        assert not any(item["path"] in PILE for item in pack["items"])


class TestCriterion6RenameAndDelete:
    def test_a_rename_leaves_one_record_at_the_new_path(self, indexed: Run, vault: Path) -> None:
        (vault / MEETING).rename(vault / "Projects/garden/meetings/tuesday.md")
        assert indexed("index").code == EXIT_OK
        paths = [r["path"] for r in indexed("search", "quillwort").json["results"]]
        assert paths == ["Projects/garden/meetings/tuesday.md"]

    def test_a_deletion_removes_its_record(self, indexed: Run, vault: Path) -> None:
        (vault / MEETING).unlink()
        assert indexed("index").json["foreign"]["removed"] == 1
        assert indexed("search", "quillwort").json["results"] == []


class TestCriterion7Adopt:
    def test_adopting_moves_it_home_and_the_path_record_goes(
        self, indexed: Run, vault: Path
    ) -> None:
        adopted = indexed("adopt", MEETING, "--type", "knowledge").json
        assert adopted["path"] == "30_Knowledge/Notes/2026-05-12.md"
        assert adopted["moved_from"] == MEETING
        text = (vault / adopted["path"]).read_text(encoding="utf-8")
        assert text.endswith("---\n" + PILE[MEETING])

        assert indexed("index").code == EXIT_OK
        (result,) = indexed("search", "quillwort").json["results"]
        assert result["id"] == adopted["id"]
        assert result["path"] == adopted["path"]
        assert indexed("concept", "get", adopted["id"]).code == EXIT_OK

    def test_a_writer_s_value_that_does_not_fit_is_set_aside(self, indexed: Run) -> None:
        # `status` is registered, and for knowledge it is draft, stable or
        # deprecated. The writer's `active` is kept under extensions.adopted
        # (core/02 section 3.3), and the concept is valid without it.
        adopted = indexed("adopt", RAISED_BEDS, "--type", "knowledge").json
        assert adopted["set_aside"] == ["status"]
        frontmatter = indexed("concept", "get", adopted["id"]).json["frontmatter"]
        assert "status" not in frontmatter
        assert frontmatter["extensions"]["adopted"] == {"status": "active"}

    def test_the_writer_s_other_keys_survive_adoption(self, indexed: Run) -> None:
        adopted = indexed(
            "adopt", RAISED_BEDS, "--type", "knowledge", "--field", "status=draft"
        ).json
        frontmatter = indexed("concept", "get", adopted["id"]).json["frontmatter"]
        assert frontmatter["tags"] == ["garden"]
        assert frontmatter["soil"] == "loam"
        assert frontmatter["status"] == "draft"

    def test_an_occupied_destination_is_refused(self, indexed: Run, vault: Path) -> None:
        (vault / "30_Knowledge/Notes/raised-beds.md").write_text("# Mine\n", encoding="utf-8")
        refused = indexed("adopt", RAISED_BEDS, "--type", "knowledge")
        assert refused.code == EXIT_FAILED
        assert untouched(vault)

    def test_without_a_type_the_refusal_names_the_candidates(
        self, indexed: Run, api: TestClient, vault: Path
    ) -> None:
        refused = indexed("adopt", RAISED_BEDS)
        assert refused.json["error"]["details"]["candidates"] == ["knowledge", "map"]
        response = api.post("/v1/concepts/adopt", json={"path": RAISED_BEDS})
        assert response.json()["error"]["details"]["candidates"] == ["knowledge", "map"]
        assert untouched(vault)

    def test_the_api_adopts_the_same_way(self, api: TestClient, vault: Path) -> None:
        response = api.post("/v1/concepts/adopt", json={"path": BOOK, "type": "knowledge"})
        assert response.status_code == 200, response.text
        assert response.json()["path"] == "30_Knowledge/Notes/thinking-fast-and-slow.md"
        assert response.json()["moved_from"] == BOOK
        assert not (vault / BOOK).exists()

    def test_the_vault_is_healthy_afterwards(self, indexed: Run) -> None:
        assert indexed("adopt", MEETING, "--type", "knowledge").code == EXIT_OK
        indexed("index")
        assert indexed("doctor").json["healthy"] is True


class TestCriterion8InsideARoot:
    def test_a_hand_written_note_under_a_root_stays_where_it_is(
        self, indexed: Run, vault: Path
    ) -> None:
        (vault / "30_Knowledge/Notes/soil.md").write_text("# Soil\n", encoding="utf-8")
        adopted = indexed("adopt", "30_Knowledge/Notes/soil.md").json
        assert adopted["path"] == "30_Knowledge/Notes/soil.md"
        assert "moved_from" not in adopted
