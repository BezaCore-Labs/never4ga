"""The in-process CLI.

details/api-cli-mcp-contract.md section 1 requires the CLI to be a thin client
over the application services, and section 4 requires human and JSON output
modes. These tests drive `main()` directly, in the same process.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path


Run = Callable[..., "Result"]


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out)


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        argv = ["--vault", str(vault), *(["--json"] if as_json else []), *arguments]
        code = main(argv)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def initialized(run: Run) -> Run:
    run("init")
    return run


class TestInit:
    def test_it_creates_a_vault(self, run: Run, vault: Path) -> None:
        result = run("init")
        assert result.code == EXIT_OK
        assert (vault / "50_System" / "system.md").is_file()
        assert "Initialized vault" in result.out

    def test_json_output_reports_the_vault_id(self, run: Run) -> None:
        result = run("init", as_json=True)
        payload = result.json
        assert payload["already_initialized"] is False
        assert len(payload["vault_id"]) == 36
        assert "50_System/system.md" in payload["created"]

    def test_running_it_twice_is_safe(self, run: Run) -> None:
        first = run("init", as_json=True).json
        second = run("init", as_json=True).json
        assert second["already_initialized"] is True
        assert second["created"] == []
        assert second["vault_id"] == first["vault_id"]

    def test_a_pile_is_reported_and_left_where_it_is(self, run: Run, vault: Path) -> None:
        # Existing Markdown directories are foreign material: named, counted
        # and left untouched.
        (vault / "Projects").mkdir()
        (vault / "Projects" / "one.md").write_text("# One\n", encoding="utf-8")
        (vault / "Projects" / "two.md").write_text("# Two\n", encoding="utf-8")
        (vault / "Reading").mkdir()
        (vault / "Reading" / "book.md").write_text("# Book\n", encoding="utf-8")

        result = run("init")
        assert result.code == EXIT_OK
        assert "foreign:   2 directories left in place" in result.out
        assert "Projects (2 files)" in result.out
        assert "Reading (1 file)" in result.out
        assert (vault / "Projects" / "one.md").read_text(encoding="utf-8") == "# One\n"

    def test_the_json_payload_names_the_foreign_material(self, run: Run, vault: Path) -> None:
        (vault / "Projects").mkdir()
        (vault / "Projects" / "one.md").write_text("# One\n", encoding="utf-8")
        payload = run("init", as_json=True).json
        assert payload["foreign_material"] == [{"directory": "Projects", "files": 1}]
        assert payload["updated"] == []

    def test_a_pile_is_told_how_to_be_reached(self, run: Run, vault: Path) -> None:
        # On a first run `search` is what reaches a pile, and a Context Pack
        # does so once a workspace exists and is mapped. `init` says both,
        # because the run that finds somebody's notes is where they ask what now.
        (vault / "Projects").mkdir()
        (vault / "Projects" / "one.md").write_text("# One\n", encoding="utf-8")

        result = run("init")
        assert result.code == EXIT_OK
        assert "next:" in result.out
        assert "never4ga index" in result.out
        assert "never4ga workspace create" in result.out
        assert "never4ga workspace map" in result.out

    def test_the_next_steps_are_in_the_payload(self, run: Run, vault: Path) -> None:
        (vault / "Projects").mkdir()
        (vault / "Projects" / "one.md").write_text("# One\n", encoding="utf-8")
        steps = run("init", as_json=True).json["next_steps"]
        assert [one["command"] for one in steps] == [
            "never4ga index",
            "never4ga workspace create <Name> --type <type>",
            "never4ga workspace map <workspace-id> --repo <path>",
        ]
        assert all(one["reason"] for one in steps)

    def test_the_next_steps_are_commands_that_exist(self, run: Run, vault: Path) -> None:
        # A hint that omits a required argument, such as --type on
        # `workspace create`, sends the user into a usage error. Parse each
        # command with its placeholders filled: a usage error means the hint is
        # wrong.
        (vault / "Projects").mkdir()
        (vault / "Projects" / "one.md").write_text("# One\n", encoding="utf-8")
        steps = run("init", as_json=True).json["next_steps"]

        substitutions = {
            "<Name>": "Baking",
            "<type>": "personal_project",
            "<workspace-id>": "01a0dac5-8e1e-77df-ab3e-27adf01a4239",
            "<path>": str(vault),
        }
        for step in steps:
            words = step["command"].split()
            assert words[0] == "never4ga"
            filled = [substitutions.get(word, word) for word in words[1:]]
            assert all(not one.startswith("<") for one in filled), step["command"]
            result = run(*filled, as_json=True)
            assert result.code != EXIT_USAGE, f"{step['command']} is not a command"

    def test_an_empty_vault_is_given_no_next_steps(self, run: Run) -> None:
        # Nothing was found, so there is nothing to be reached. A vault with no
        # pile gets the report it always got.
        result = run("init", as_json=True)
        assert result.json["next_steps"] == []
        assert "next:" not in result.out

    def test_status_names_the_foreign_material_too(self, run: Run, vault: Path) -> None:
        (vault / "Projects").mkdir()
        run("init")
        assert run("status", as_json=True).json["foreign_material"] == ["Projects"]

    def test_a_title_can_be_given(self, run: Run) -> None:
        run("init", "--title", "Alex's Vault")
        assert run("status", as_json=True).json["title"] == "Alex's Vault"

    def test_a_missing_vault_directory_is_a_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--vault", str(tmp_path / "nope"), "init"])
        assert code == EXIT_USAGE
        assert "not a directory" in capsys.readouterr().err


class TestStatus:
    def test_an_uninitialized_directory_says_so(self, run: Run) -> None:
        result = run("status", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["initialized"] is False

    def test_it_counts_concepts_by_type(self, initialized: Run) -> None:
        initialized("knowledge", "create", "Hybrid Retrieval")
        payload = initialized("status", as_json=True).json
        assert payload["concepts_by_type"]["knowledge"] == 1
        assert payload["concepts_by_type"]["system_manifest"] == 1
        assert payload["concepts_by_type"]["dashboard"] == 1


class TestDoctor:
    def test_a_fresh_vault_is_healthy(self, initialized: Run) -> None:
        result = initialized("doctor", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["healthy"] is True
        assert result.json["findings"] == []

    def test_an_unhealthy_vault_exits_non_zero(self, run: Run) -> None:
        result = run("doctor", as_json=True)
        assert result.code == EXIT_FAILED
        assert result.json["healthy"] is False

    def test_findings_carry_a_code_and_a_hint(self, run: Run) -> None:
        findings = run("doctor", as_json=True).json["findings"]
        assert findings
        for finding in findings:
            assert finding["code"]
            assert finding["repair_hint"]
            assert finding["severity"] in {"error", "warning"}

    def test_a_duplicate_id_is_surfaced(self, initialized: Run, vault: Path) -> None:
        initialized("knowledge", "create", "Original")
        source = vault / "30_Knowledge" / "Notes" / "original.md"
        (vault / "30_Knowledge" / "Notes" / "copy.md").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
        result = initialized("doctor", as_json=True)
        assert result.code == EXIT_FAILED
        assert "duplicate_id" in {f["code"] for f in result.json["findings"]}

    def test_the_level_can_be_lowered(self, initialized: Run) -> None:
        assert initialized("doctor", "--level", "core", as_json=True).code == EXIT_OK


class TestValidate:
    def test_a_fresh_vault_validates(self, initialized: Run) -> None:
        result = initialized("validate", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["checked"] == result.json["valid"]

    def test_a_single_path_can_be_checked(self, initialized: Run) -> None:
        result = initialized("validate", "50_System/system.md", as_json=True)
        assert result.json["checked"] == 1

    def test_an_invalid_concept_fails(self, initialized: Run, vault: Path) -> None:
        (vault / "30_Knowledge" / "Notes" / "Broken.md").write_text(
            "---\ntype: knowledge\nid: 0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31\n"
            "schema: never4ga/0.1\ntitle: Broken\ncreated_at: not-a-time\n---\nbody\n",
            encoding="utf-8",
        )
        result = initialized("validate", as_json=True)
        assert result.code == EXIT_FAILED
        codes = {
            issue["code"] for document in result.json["documents"] for issue in document["issues"]
        }
        assert "invalid_timestamp" in codes

    # A validator that silently omits what it cannot read is unsafe: a
    # document with an unescaped quote in a description must be counted and
    # reported, not skipped.

    def test_a_document_that_cannot_be_parsed_is_reported(
        self, initialized: Run, vault: Path
    ) -> None:
        (vault / "30_Knowledge" / "Notes" / "unparseable.md").write_text(
            '---\ntype: knowledge\ndescription: "What "done" means"\n---\nbody\n',
            encoding="utf-8",
        )
        result = initialized("validate", as_json=True)
        assert result.code == EXIT_FAILED
        codes = {
            issue["code"] for document in result.json["documents"] for issue in document["issues"]
        }
        assert codes, "the unreadable document was skipped silently"

    def test_an_unreadable_document_is_counted_as_checked(
        self, initialized: Run, vault: Path
    ) -> None:
        clean = initialized("validate", as_json=True).json["checked"]
        (vault / "30_Knowledge" / "Notes" / "unparseable.md").write_text(
            '---\ntype: knowledge\ndescription: "What "done" means"\n---\nbody\n',
            encoding="utf-8",
        )
        result = initialized("validate", as_json=True)
        assert result.json["checked"] == clean + 1
        assert result.json["valid"] == clean


class TestWorkspaces:
    def test_a_workspace_can_be_created(self, initialized: Run, vault: Path) -> None:
        result = initialized(
            "workspace", "create", "BezaCore", "--type", "organization", as_json=True
        )
        assert result.code == EXIT_OK
        assert result.json["path"] == "10_Workspaces/BezaCore/workspace.md"
        assert (vault / "10_Workspaces" / "BezaCore" / "workspace.md").is_file()

    def test_a_child_workspace_can_be_created(self, initialized: Run) -> None:
        parent = initialized(
            "workspace", "create", "BezaCore", "--type", "organization", as_json=True
        ).json
        child = initialized(
            "workspace",
            "create",
            "Sparrow",
            "--type",
            "product",
            "--parent",
            parent["id"],
            as_json=True,
        ).json
        assert child["path"] == "10_Workspaces/BezaCore/Workspaces/Sparrow/workspace.md"

    def test_a_workspace_can_declare_profiles_and_repositories(
        self, initialized: Run, vault: Path
    ) -> None:
        # Importing an existing workspace needs both, and neither should need
        # a hand edit afterwards.
        result = initialized(
            "workspace",
            "create",
            "Never4gA",
            "--type",
            "product",
            "--profile",
            "never4ga/workspace/software/0.1",
            "--repository",
            "never4ga",
            "--repository",
            "never4ga-docs",
            as_json=True,
        )
        assert result.code == EXIT_OK
        text = (vault / "10_Workspaces" / "Never4gA" / "workspace.md").read_text()
        assert "never4ga/workspace/base/0.1" in text
        assert "never4ga/workspace/software/0.1" in text
        assert "- never4ga-docs" in text

    def test_the_base_profile_is_always_present(self, initialized: Run, vault: Path) -> None:
        # Naming a profile adds to the base, never replaces it: every
        # workspace carries the base profile (core/03 section 8).
        initialized(
            "workspace",
            "create",
            "Never4gA",
            "--type",
            "product",
            "--profile",
            "never4ga/workspace/software/0.1",
        )
        text = (vault / "10_Workspaces" / "Never4gA" / "workspace.md").read_text()
        assert text.index("never4ga/workspace/base/0.1") < text.index(
            "never4ga/workspace/software/0.1"
        )

    def test_an_unknown_profile_is_preserved_and_warned_about(
        self, initialized: Run, vault: Path
    ) -> None:
        # core/02 section 19.2: unknown profiles MUST be preserved, and section
        # 36 makes them a warning. A profile from a future version, or from
        # another tool, has to be usable before Never4gA knows it.
        result = initialized(
            "workspace",
            "create",
            "Never4gA",
            "--type",
            "product",
            "--profile",
            "never4ga/workspace/nonsense/0.1",
        )
        assert result.code == EXIT_OK
        assert "nonsense" in (vault / "10_Workspaces" / "Never4gA" / "workspace.md").read_text()
        report = initialized("validate", as_json=True)
        codes = [i["code"] for d in report.json["documents"] for i in d["issues"]]
        assert "unregistered_profile" in codes

    def test_a_created_workspace_with_extras_is_valid(self, initialized: Run) -> None:
        initialized(
            "workspace",
            "create",
            "Never4gA",
            "--type",
            "product",
            "--profile",
            "never4ga/workspace/software/0.1",
            "--repository",
            "never4ga",
        )
        report = initialized("validate", as_json=True)
        assert report.json["valid"] == report.json["checked"]

    def test_workspaces_can_be_listed(self, initialized: Run) -> None:
        initialized("workspace", "create", "BezaCore", "--type", "organization")
        initialized("workspace", "create", "Household", "--type", "operations")
        listed = initialized("workspace", "list", as_json=True).json["workspaces"]
        assert {entry["title"] for entry in listed} == {"BezaCore", "Household"}

    def test_an_empty_vault_lists_no_workspaces(self, initialized: Run) -> None:
        result = initialized("workspace", "list", as_json=True)
        assert result.json["workspaces"] == []

    def test_a_workspace_can_be_shown(self, initialized: Run) -> None:
        created = initialized(
            "workspace", "create", "BezaCore", "--type", "organization", as_json=True
        ).json
        shown = initialized("workspace", "show", created["id"], as_json=True).json
        assert shown["frontmatter"]["title"] == "BezaCore"
        assert shown["directory"] == "10_Workspaces/BezaCore"

    def test_showing_an_unknown_workspace_fails_cleanly(self, initialized: Run) -> None:
        result = initialized(
            "workspace", "show", "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31", as_json=True
        )
        assert result.code == EXIT_FAILED
        assert json.loads(result.err)["error"]["code"] == "workspace_not_found"

    def test_a_duplicate_workspace_is_refused_with_a_structured_error(
        self, initialized: Run
    ) -> None:
        initialized("workspace", "create", "BezaCore", "--type", "organization")
        result = initialized(
            "workspace", "create", "BezaCore", "--type", "organization", as_json=True
        )
        assert result.code == EXIT_FAILED
        error = json.loads(result.err)["error"]
        assert error["code"] == "concept_not_created"
        assert error["retryable"] is False
        assert "already exists" in error["message"]


class TestCreatingConcepts:
    def test_a_knowledge_note(self, initialized: Run) -> None:
        result = initialized(
            "knowledge",
            "create",
            "Hybrid Retrieval",
            "--description",
            "Lexical plus semantic.",
            "--tag",
            "retrieval",
            as_json=True,
        )
        assert result.json["path"] == "30_Knowledge/Notes/hybrid-retrieval.md"

    def test_a_person(self, initialized: Run) -> None:
        initialized("life-area", "create", "Relationships")
        result = initialized(
            "concept",
            "create",
            "person",
            "Ada Lovelace",
            "--in",
            "20_Life/Relationships",
            as_json=True,
        )
        assert result.json["path"] == "20_Life/Relationships/ada-lovelace.md"

    def test_a_map(self, initialized: Run) -> None:
        result = initialized("map", "create", "Artificial Intelligence", as_json=True)
        assert result.json["path"] == "30_Knowledge/Maps/artificial-intelligence.md"

    def test_a_life_area(self, initialized: Run) -> None:
        result = initialized("life-area", "create", "Health", as_json=True)
        assert result.json["path"] == "20_Life/Health/area.md"

    def test_any_registered_type_through_one_verb(self, initialized: Run) -> None:
        result = initialized("concept", "create", "knowledge", "Hybrid Retrieval", as_json=True)
        assert result.json["path"] == "30_Knowledge/Notes/hybrid-retrieval.md"

    def test_the_inferred_folder_is_reported(self, initialized: Run) -> None:
        result = initialized("concept", "create", "knowledge", "Hybrid Retrieval")
        assert "30_Knowledge/Notes" in result.out

    def test_a_workspace_scoped_type(self, initialized: Run) -> None:
        workspace = initialized(
            "workspace", "create", "BezaCore", "--type", "organization", as_json=True
        ).json["id"]
        result = initialized(
            "concept",
            "create",
            "decision",
            "Use SQLite First",
            "--workspace",
            workspace,
            as_json=True,
        )
        assert result.json["path"] == "10_Workspaces/BezaCore/Decisions/use-sqlite-first.md"

    def test_fields_reach_the_frontmatter(self, initialized: Run, vault: Path) -> None:
        initialized(
            "concept",
            "create",
            "knowledge",
            "Hybrid Retrieval",
            "--field",
            "tags=retrieval",
            "--field",
            "tags=sqlite",
        )
        text = (vault / "30_Knowledge" / "Notes" / "hybrid-retrieval.md").read_text(
            encoding="utf-8"
        )
        assert "- retrieval" in text
        assert "- sqlite" in text

    def test_a_malformed_field_is_refused(self, initialized: Run) -> None:
        result = initialized("concept", "create", "knowledge", "Note", "--field", "tags")
        assert result.code == EXIT_FAILED
        assert "name=value" in result.err

    def test_a_guess_is_refused_rather_than_made(self, initialized: Run) -> None:
        workspace = initialized(
            "workspace", "create", "BezaCore", "--type", "organization", as_json=True
        ).json["id"]
        result = initialized("concept", "create", "standard", "Palette", "--workspace", workspace)
        assert result.code == EXIT_FAILED
        assert "--in" in result.err

    def test_a_named_folder_is_used(self, initialized: Run) -> None:
        workspace = initialized(
            "workspace", "create", "BezaCore", "--type", "organization", as_json=True
        ).json["id"]
        result = initialized(
            "concept",
            "create",
            "standard",
            "Palette",
            "--workspace",
            workspace,
            "--in",
            "10_Workspaces/BezaCore/Brand",
            as_json=True,
        )
        assert result.json["path"] == "10_Workspaces/BezaCore/Brand/palette.md"

    def test_everything_created_leaves_the_vault_healthy(self, initialized: Run) -> None:
        initialized("workspace", "create", "BezaCore", "--type", "organization")
        initialized("knowledge", "create", "Hybrid Retrieval")
        initialized("map", "create", "Retrieval")
        initialized("life-area", "create", "Health")
        initialized("concept", "create", "person", "Ada Lovelace", "--in", "20_Life/Health")
        assert initialized("doctor", as_json=True).code == EXIT_OK


class TestOutputContract:
    def test_human_output_is_not_json(self, initialized: Run) -> None:
        result = initialized("status")
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.out)

    def test_json_output_is_parseable_for_every_read_command(self, initialized: Run) -> None:
        for command in (["status"], ["doctor"], ["validate"], ["workspace", "list"]):
            result = initialized(*command, as_json=True)
            assert isinstance(result.json, dict), command

    def test_errors_go_to_stderr_and_leave_stdout_clean(self, run: Run) -> None:
        result = run(
            "workspace",
            "create",
            "X",
            "--type",
            "product",
            "--parent",
            "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31",
            as_json=True,
        )
        assert result.code == EXIT_FAILED
        assert result.out == ""
        assert json.loads(result.err)["error"]["code"] == "concept_not_created"

    def test_a_structured_error_carries_the_contract_fields(self, run: Run) -> None:
        result = run(
            "workspace",
            "create",
            "X",
            "--type",
            "product",
            "--parent",
            "0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31",
            as_json=True,
        )
        error = json.loads(result.err)["error"]
        assert set(error) == {"code", "message", "details", "retryable", "repair_hint"}


class TestTheGeneratedActor:
    """core/02 section 5.2: agent-created canonical content MUST say so.

    Without `--actor`, every document an agent wrote through the CLI would
    assert `human:owner`, the one claim about a file that Never4gA should never
    make on a model's behalf.
    """

    def _frontmatter(self, vault: Path, relative: str) -> dict[str, Any]:
        import ruamel.yaml

        text = (vault / relative).read_text(encoding="utf-8")
        _, _, rest = text.partition("---\n")
        body, _, _ = rest.partition("\n---")
        loaded = ruamel.yaml.YAML(typ="safe").load(body)
        assert isinstance(loaded, dict)
        return loaded

    def test_it_defaults_to_the_owner(self, initialized: Run, vault: Path) -> None:
        initialized("knowledge", "create", "Hybrid Retrieval")
        frontmatter = self._frontmatter(vault, "30_Knowledge/Notes/hybrid-retrieval.md")
        assert frontmatter["generated"] == {
            "by": "human:owner",
            "at": frontmatter["created_at"],
        }

    def test_a_named_actor_is_written(self, initialized: Run, vault: Path) -> None:
        initialized("--actor", "claude-code/claude-opus-5", "knowledge", "create", "Hybrid")
        frontmatter = self._frontmatter(vault, "30_Knowledge/Notes/hybrid.md")
        assert frontmatter["generated"]["by"] == "claude-code/claude-opus-5"

    def test_it_reaches_the_generic_creation_verb_too(self, initialized: Run, vault: Path) -> None:
        initialized(
            "--actor",
            "codex/gpt-5",
            "concept",
            "create",
            "knowledge",
            "Something",
        )
        frontmatter = self._frontmatter(vault, "30_Knowledge/Notes/something.md")
        assert frontmatter["generated"]["by"] == "codex/gpt-5"

    def test_an_empty_actor_is_refused_rather_than_written(self, initialized: Run) -> None:
        # An actor that says nothing is worse than the default: it looks
        # deliberate. core/02 wants the field to mean something.
        assert initialized("--actor", "   ", "knowledge", "create", "Thing").code == EXIT_FAILED


def _write_config(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Put a machine-local config where PlatformPaths will find it."""
    from never4ga.config import CONFIG_FILENAME
    from never4ga.platform_paths import PlatformPaths

    directory = PlatformPaths.resolve().config
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CONFIG_FILENAME).write_text(body, encoding="utf-8")


class TestVaultResolution:
    def test_the_environment_variable_is_used_when_no_flag_is_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NEVER4GA_VAULT", str(tmp_path))
        assert main(["init"]) == EXIT_OK
        assert (tmp_path / "50_System" / "system.md").is_file()

    def test_the_flag_wins_over_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setenv("NEVER4GA_VAULT", str(tmp_path))
        main(["--vault", str(elsewhere), "init"])
        assert (elsewhere / "50_System" / "system.md").is_file()
        assert not (tmp_path / "50_System").exists()

    def test_the_config_file_names_the_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # core/05 section 9 lists "vault filesystem path" first among machine-local
        # configuration. Without this the key parses, documents itself and is
        # read by nothing, and a shell that has lost NEVER4GA_VAULT silently
        # operates on the current directory instead.
        configured = tmp_path / "configured"
        configured.mkdir()
        _write_config(monkeypatch, f'vault = "{configured}"\n')

        assert main(["init"]) == EXIT_OK
        assert (configured / "50_System" / "system.md").is_file()

    def test_the_environment_wins_over_the_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = tmp_path / "configured"
        configured.mkdir()
        chosen = tmp_path / "chosen"
        chosen.mkdir()
        _write_config(monkeypatch, f'vault = "{configured}"\n')
        monkeypatch.setenv("NEVER4GA_VAULT", str(chosen))

        main(["init"])
        assert (chosen / "50_System" / "system.md").is_file()
        assert not (configured / "50_System").exists()

    def test_the_flag_wins_over_the_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = tmp_path / "configured"
        configured.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        _write_config(monkeypatch, f'vault = "{configured}"\n')

        main(["--vault", str(elsewhere), "init"])
        assert (elsewhere / "50_System" / "system.md").is_file()
        assert not (configured / "50_System").exists()

    def test_a_config_naming_no_vault_still_falls_back_to_the_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        here = tmp_path / "here"
        here.mkdir()
        _write_config(monkeypatch, "[service]\nport = 7399\n")
        monkeypatch.chdir(here)

        main(["init"])
        assert (here / "50_System" / "system.md").is_file()
