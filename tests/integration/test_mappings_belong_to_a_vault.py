"""A repository mapped from one vault is not mapped in another.

Each vault keeps its own mappings beside its session store. A shared registry
would let a repository mapped from a scratch vault resolve from another vault:
`workspace resolve` would name a workspace path that vault does not have,
`context startup` would build an empty pack and exit 0, and `doctor` would
report on a repository the vault has never heard of.

The older machine-wide file is only read to adopt its entries. Each entry goes
to the vault that holds the workspace it names, and nowhere else.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

import pytest

from never4ga.adapters.filesystem import WorkspaceMappingFile
from never4ga.cli import EXIT_OK, main
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import WorkspaceMapping
from never4ga.platform_paths import PlatformPaths


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    @property
    def json(self) -> Any:
        return json.loads(self.out)


Run = Callable[..., Result]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Callable[[Path], Run]:
    def against(vault: Path) -> Run:
        def invoke(*arguments: str, as_json: bool = False) -> Result:
            argv = ["--vault", str(vault), "--local", *(["--json"] if as_json else [])]
            code = main([*argv, *arguments])
            captured = capsys.readouterr()
            return Result(code, captured.out, captured.err)

        return invoke

    return against


def _vault(tmp_path: Path, name: str, cli: Callable[[Path], Run]) -> Run:
    root = tmp_path / name
    root.mkdir()
    run = cli(root)
    assert run("init").code == EXIT_OK
    return run


@pytest.fixture
def ours(tmp_path: Path, cli: Callable[[Path], Run]) -> Run:
    return _vault(tmp_path, "ours", cli)


@pytest.fixture
def scratch(tmp_path: Path, cli: Callable[[Path], Run]) -> Run:
    return _vault(tmp_path, "scratch", cli)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "baking"
    (root / ".git").mkdir(parents=True)
    return root


def _workspace(run: Run, title: str) -> dict[str, Any]:
    created = run("workspace", "create", title, "--type", "project", as_json=True)
    assert created.code == EXIT_OK, created.err
    return dict(created.json)


class TestAMappingStaysInItsVault:
    @pytest.fixture
    def mapped_from_scratch(self, scratch: Run, repository: Path) -> str:
        baking = _workspace(scratch, "Baking")
        assert scratch("workspace", "map", baking["id"], "--repo", str(repository)).code == EXIT_OK
        return str(baking["id"])

    def test_the_other_vault_lists_no_mapping(self, ours: Run, mapped_from_scratch: str) -> None:
        assert ours("workspace", "mappings", as_json=True).json["mappings"] == []

    def test_the_other_vault_does_not_resolve_the_repository(
        self, ours: Run, mapped_from_scratch: str, repository: Path
    ) -> None:
        result = ours("workspace", "resolve", "--path", str(repository), as_json=True)
        assert result.code != EXIT_OK
        assert "scope_unresolved" in result.out + result.err

    def test_the_other_vault_builds_no_pack_for_it(
        self, ours: Run, mapped_from_scratch: str, repository: Path
    ) -> None:
        ours("index")
        result = ours("context", "startup", "--cwd", str(repository), as_json=True)
        assert result.code != EXIT_OK
        assert "Baking" not in result.out

    def test_the_other_vault_s_doctor_says_nothing_about_it(
        self, ours: Run, mapped_from_scratch: str, repository: Path
    ) -> None:
        payload = ours("doctor", as_json=True).json
        assert not [f for f in payload["findings"] if str(repository) in json.dumps(f)]

    def test_the_vault_that_mapped_it_still_resolves_it(
        self, scratch: Run, mapped_from_scratch: str, repository: Path
    ) -> None:
        result = scratch("workspace", "resolve", "--path", str(repository), as_json=True)
        assert result.code == EXIT_OK
        assert result.json["workspace"] == mapped_from_scratch


class TestTheMachineWideFileIsAdopted:
    """Mappings in the older machine-wide file are not lost, and not shared either."""

    @pytest.fixture
    def machine_file(
        self, ours: Run, scratch: Run, tmp_path: Path
    ) -> tuple[Path, WorkspaceMapping, WorkspaceMapping]:
        ours_workspace = _workspace(ours, "Kitchen")
        theirs_workspace = _workspace(scratch, "Baking")
        mine = WorkspaceMapping(
            workspace_id=ConceptId.parse(ours_workspace["id"]),
            workspace_path=VaultPath.parse(ours_workspace["path"]),
            repository_root=PurePath(tmp_path / "Projects" / "kitchen"),
        )
        theirs = WorkspaceMapping(
            workspace_id=ConceptId.parse(theirs_workspace["id"]),
            workspace_path=VaultPath.parse(theirs_workspace["path"]),
            repository_root=PurePath(tmp_path / "Projects" / "baking"),
        )
        path = PlatformPaths.resolve().machine_workspaces_file
        WorkspaceMappingFile(path).save([mine, theirs])
        return path, mine, theirs

    def test_each_vault_sees_only_its_own_entry(
        self, ours: Run, scratch: Run, machine_file: tuple[Path, WorkspaceMapping, WorkspaceMapping]
    ) -> None:
        _, mine, theirs = machine_file
        assert [
            m["workspace"] for m in ours("workspace", "mappings", as_json=True).json["mappings"]
        ] == [str(mine.workspace_id)]
        assert [
            m["workspace"] for m in scratch("workspace", "mappings", as_json=True).json["mappings"]
        ] == [str(theirs.workspace_id)]

    def test_the_first_write_moves_the_vault_s_entries_across(
        self,
        ours: Run,
        repository: Path,
        machine_file: tuple[Path, WorkspaceMapping, WorkspaceMapping],
    ) -> None:
        path, mine, theirs = machine_file
        other = _workspace(ours, "Pantry")
        pantry = repository.parent / "pantry"
        (pantry / ".git").mkdir(parents=True)
        assert ours("workspace", "map", other["id"], "--repo", str(pantry)).code == EXIT_OK
        assert tuple(WorkspaceMappingFile(path).load()) == (theirs,)
        listed = ours("workspace", "mappings", as_json=True).json["mappings"]
        assert {m["workspace"] for m in listed} == {str(mine.workspace_id), other["id"]}
