"""`adapters` and `workspace mark` through the CLI.

The CLI is the surface a person actually touches, and two rules are about that
surface rather than about the engine: `sync` without `--apply` writes nothing,
proven by hash, and `workspace mark` refuses to rewrite a marker it did not
create.

`HOME` is redirected by the autouse fixture in `conftest.py`, so a descriptor's
`~/.claude/skills` resolves inside `tmp_path`. Nothing here can reach the real
machine's clients.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_FAILED, EXIT_OK, main

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
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = False) -> Result:
        code = main(["--vault", str(vault), *(["--json"] if as_json else []), *arguments])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def probe(vault: Path, home: Path, run: Run) -> Path:
    """A vault with Skills, and one client described only by a TOML file."""
    run("init")
    integrations = vault / "50_System" / "Integrations"
    integrations.mkdir(parents=True, exist_ok=True)
    (integrations / "probe.toml").write_text(
        'client_id = "probe"\n'
        'title = "Probe"\n'
        'global_skills_path = "~/.probe/skills"\n'
        'detect_paths = ["~/.probe"]\n'
        'measured_on = "2026-08-25"\n',
        encoding="utf-8",
    )
    (home / ".probe").mkdir(parents=True)
    return home / ".probe" / "skills"


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


class TestAdaptersList:
    def test_it_says_which_clients_are_here(self, run: Run, probe: Path) -> None:
        result = run("adapters", "list", as_json=True)
        assert result.code == EXIT_OK
        by_id = {client["id"]: client for client in result.json["clients"]}
        assert by_id["probe"]["installed"] is True
        assert by_id["probe"]["deployment"] == "copy"

    def test_a_vault_descriptor_appears_beside_the_shipped_ones(
        self, run: Run, probe: Path
    ) -> None:
        ids = {client["id"] for client in run("adapters", "list", as_json=True).json["clients"]}
        assert {"probe", "claude_code", "codex_cli", "antigravity_cli"} <= ids


class TestSyncWritesNothingWithoutApply:
    def test_the_client_directory_is_untouched(self, run: Run, probe: Path, home: Path) -> None:
        # Proven by hash. core/04 section 7 step 4 makes dry-run opt-in; this
        # inverts it, because the target is a user's home directory.
        before = tree_digest(home)
        result = run("adapters", "sync", as_json=True)
        assert result.code == EXIT_OK
        assert result.json["planned"]
        assert tree_digest(home) == before

    def test_apply_deploys(self, run: Run, probe: Path) -> None:
        assert run("adapters", "sync", "--apply", as_json=True).code == EXIT_OK
        assert (probe / "never4ga-startup" / "SKILL.md").is_file()

    def test_one_client_can_be_named(self, run: Run, probe: Path) -> None:
        run("adapters", "sync", "--client", "probe", "--apply")
        assert (probe / "never4ga-doctor" / "SKILL.md").is_file()

    def test_an_unknown_client_is_refused(self, run: Run, probe: Path) -> None:
        result = run("adapters", "sync", "--client", "nonesuch")
        assert result.code == EXIT_FAILED
        assert "nonesuch" in result.err


class TestAdaptersDoctor:
    def test_a_synced_machine_is_healthy(self, run: Run, probe: Path) -> None:
        run("adapters", "sync", "--apply")
        assert run("adapters", "doctor", as_json=True).code == EXIT_OK

    def test_a_collision_is_reported_and_fails(self, run: Run, probe: Path) -> None:
        (probe / "never4ga-doctor").mkdir(parents=True)
        (probe / "never4ga-doctor" / "SKILL.md").write_text("mine", encoding="utf-8")
        result = run("adapters", "doctor", as_json=True)
        assert result.code == EXIT_FAILED
        codes = {finding["code"] for finding in result.json["findings"]}
        assert "collision" in codes

    def test_a_locally_edited_skill_is_named_as_such(self, run: Run, probe: Path) -> None:
        run("adapters", "sync", "--apply")
        (probe / "never4ga-capture" / "SKILL.md").write_text("mine", encoding="utf-8")
        findings = run("adapters", "doctor", as_json=True).json["findings"]
        edited = [f for f in findings if f["name"] == "never4ga-capture"]
        assert edited and edited[0]["detail"] == "locally_edited_generated_skill"


class TestWorkspaceMark:
    @pytest.fixture
    def repository(self, tmp_path: Path, run: Run) -> Path:
        root = tmp_path / "Projects" / "thing"
        (root / ".git").mkdir(parents=True)
        run("init")
        workspace = run("workspace", "create", "Thing", "--type", "project", as_json=True).json[
            "id"
        ]
        run("workspace", "map", workspace, "--repo", str(root))
        return root

    def test_it_shows_before_it_writes(self, run: Run, repository: Path) -> None:
        # details/security-configuration.md section 7.1 condition 3: a diff is
        # shown before anything is written.
        result = run("workspace", "mark", "--repo", str(repository), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["written"] is False
        assert not (repository / ".never4ga.toml").exists()

    def test_apply_writes_the_marker(self, run: Run, repository: Path) -> None:
        run("workspace", "mark", "--repo", str(repository), "--apply")
        marker = repository / ".never4ga.toml"
        assert marker.is_file()
        assert marker.read_text(encoding="utf-8").startswith("workspace = ")

    def test_an_unmapped_repository_is_never_written_to(self, run: Run, tmp_path: Path) -> None:
        # Condition 6, which is condition 1 seen from the other side.
        stranger = tmp_path / "Projects" / "someone-elses"
        (stranger / ".git").mkdir(parents=True)
        run("init")
        result = run("workspace", "mark", "--repo", str(stranger), "--apply")
        assert result.code == EXIT_FAILED
        assert not (stranger / ".never4ga.toml").exists()

    def test_a_marker_never4ga_did_not_write_is_refused(self, run: Run, repository: Path) -> None:
        # Section 7.1 condition 5: once the file exists it is an existing file,
        # and an existing file Never4gA cannot prove it owns is not its to
        # replace.
        marker = repository / ".never4ga.toml"
        marker.write_text('workspace = "not-a-uuid"\n', encoding="utf-8")
        result = run("workspace", "mark", "--repo", str(repository), "--apply")
        assert result.code == EXIT_FAILED
        assert marker.read_text(encoding="utf-8") == 'workspace = "not-a-uuid"\n'

    def test_one_it_did_write_can_be_rewritten(self, run: Run, repository: Path) -> None:
        run("workspace", "mark", "--repo", str(repository), "--apply")
        result = run("workspace", "mark", "--repo", str(repository), "--apply")
        assert result.code == EXIT_OK

    def test_the_marker_is_recorded_in_the_ownership_manifest(
        self, run: Run, repository: Path, tmp_path: Path
    ) -> None:
        # Condition 4: ownership and content hash are recorded.
        run("workspace", "mark", "--repo", str(repository), "--apply")
        manifest = json.loads(
            (tmp_path / "platform" / "state_home" / "never4ga" / "deployments.json").read_text(
                encoding="utf-8"
            )
        )
        markers = [
            entry for entry in manifest["deployments"] if entry["client_id"] == "repository_marker"
        ]
        assert len(markers) == 1
        assert markers[0]["source_hash"]


class TestSkillsNew:
    """A Skill we author starts from the template, with its provenance."""

    def test_it_writes_the_skill_and_its_record(self, run: Run, probe: Path, vault: Path) -> None:
        result = run(
            "skills",
            "new",
            "release-notes",
            "--description",
            "Writes release notes: from merged pull requests.",
            as_json=True,
        )
        assert result.code == EXIT_OK
        skill = vault / "50_System" / "Skills" / "release-notes" / "SKILL.md"
        assert skill.is_file()
        assert "name: release-notes" in skill.read_text()
        assert "tree_digest" in (vault / str(result.json["provenance"])).read_text()

    def test_the_vault_stays_healthy_and_the_skill_is_not_judged(
        self, run: Run, probe: Path
    ) -> None:
        # A generator, not a gate: nothing in `doctor` has an opinion about a
        # Skill full of placeholders, and `skills status` does not list it.
        run("skills", "new", "release-notes", "--description", "Writes release notes.")
        assert run("index").code == EXIT_OK
        assert run("doctor", as_json=True).json["healthy"] is True
        listed = {s["name"] for s in run("skills", "status", as_json=True).json["skills"]}
        assert "release-notes" not in listed

    def test_a_second_start_is_refused(self, run: Run, probe: Path) -> None:
        run("skills", "new", "release-notes", "--description", "Writes release notes.")
        again = run("skills", "new", "release-notes", "--description", "Again.")
        assert again.code == EXIT_FAILED
        assert "already exists" in (again.out + again.err)
