"""Every composition root must assemble the same thing (core/05 section 15).

The HTTP API, the CLI and the MCP server call the same application services,
and they also have to *assemble* those services the same way. A second assembly
written by hand in a second interface is where two roots drift apart. A root
that assembles less than its siblings still reports success; it silently does
less work.

`test_layering.py` cannot catch that, because the layering stays correct. What
goes wrong is the *contents* of assemblies that are supposed to match, which
only a test comparing them can see.

There are three assemblies, not two. MCP is an interface rather than a
composition root and may not import one, so it builds its own.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import (
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
    WorkspaceMappingFile,
)
from never4ga.build import STARTUP_BUILD_ID
from never4ga.cli import main
from never4ga.config import ServiceEndpoint
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import WorkspaceMapping
from never4ga.layout import SYSTEM_MANIFEST
from never4ga.platform_paths import PlatformPaths
from never4ga.service.runtime import ServiceSettings, VaultRuntime
from never4ga.services import VaultInitializer


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Parity"
    )
    return root


def _workspace_in(vault: Path, name: str) -> WorkspaceMapping:
    workspace_id = ConceptId.new()
    workspace = vault / "10_Workspaces" / name
    workspace.mkdir(parents=True)
    (workspace / "workspace.md").write_text(
        f"---\ntype: workspace\nid: {workspace_id}\nschema: never4ga/0.1\n"
        f'title: {name}\ncreated_at: "2026-08-01T12:00:00Z"\n'
        f"lifecycle: active\nworkspace_type: product\n---\n\n# {name}\n"
    )
    return WorkspaceMapping(
        workspace_id=workspace_id,
        workspace_path=VaultPath.parse(f"10_Workspaces/{name}/workspace.md"),
    )


def _map_in_the_machine_file(workspace: WorkspaceMapping, repository: Path) -> None:
    store = WorkspaceMappingFile(PlatformPaths.resolve().machine_workspaces_file)
    store.save([*store.load(), replace(workspace, repository_root=repository)])


class TestDoctorReachesTheRepositoryFromEveryRoot:
    """`doctor`'s repository checks must be wired in all three roots.

    The repository checks reach outside the vault, and they are wired by hand
    in three places. A root that forgot them would report *fewer* findings
    rather than failing.

    So this asserts on a finding rather than on an attribute: a repository whose
    `AGENTS.md` names a path that is not there, and three roots that must all
    say so.
    """

    @pytest.fixture
    def mapped_repository(self, vault: Path, tmp_path: Path) -> Path:
        repository = tmp_path / "acme"
        (repository / ".git").mkdir(parents=True)
        # A link target, not a code span: only a link is a reference the
        # check follows.
        (repository / "AGENTS.md").write_text("Read [the guide](docs/vanished.md) first.\n")
        # Written to the older machine-wide mapping file, so every root has to
        # adopt it as well as read it.
        _map_in_the_machine_file(_workspace_in(vault, "Acme"), repository)
        return repository

    def test_the_service_root_reports_it(self, vault: Path, mapped_repository: Path) -> None:
        runtime = VaultRuntime(ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0)))
        runtime.start()
        try:
            with runtime.sessions() as session:
                codes = [f.code for f in session.doctor().diagnose().findings]
        finally:
            runtime.stop()
        assert "repository_reference_broken" in codes

    def test_the_cli_root_reports_it(
        self, vault: Path, mapped_repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["--vault", str(vault), "--local", "--json", "doctor"])
        assert "repository_reference_broken" in capsys.readouterr().out

    def test_the_mcp_assembly_reports_it(self, vault: Path, mapped_repository: Path) -> None:
        from never4ga.mcp.toolbox import Toolbox

        payload = Toolbox(vault).doctor({"level": "strict"})
        codes = [finding["code"] for finding in payload["findings"]]
        assert "repository_reference_broken" in codes


class TestNoRootReadsAnotherVaultsMapping:
    """A mapping made from another vault is ignored, in all three assemblies.

    A repository mapped from another vault sits in the machine-wide file from
    before mappings were per vault. The workspace it names is not in this
    vault, so no root may adopt it. A root that did would report agent-file
    findings about a repository this vault never mapped.
    """

    @pytest.fixture
    def elsewhere(self, tmp_path: Path) -> Path:
        other = tmp_path / "other"
        other.mkdir()
        repository = tmp_path / "baking"
        (repository / ".git").mkdir(parents=True)
        _map_in_the_machine_file(_workspace_in(other, "Baking"), repository)
        return repository

    def test_the_service_root_ignores_it(self, vault: Path, elsewhere: Path) -> None:
        runtime = VaultRuntime(ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0)))
        runtime.start()
        try:
            with runtime.sessions() as session:
                findings = session.doctor().diagnose().findings
        finally:
            runtime.stop()
        assert not [f for f in findings if str(elsewhere) in f.message]

    def test_the_cli_root_ignores_it(
        self, vault: Path, elsewhere: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["--vault", str(vault), "--local", "--json", "doctor"])
        assert str(elsewhere) not in capsys.readouterr().out

    def test_the_mcp_assembly_ignores_it(self, vault: Path, elsewhere: Path) -> None:
        from never4ga.mcp.toolbox import Toolbox

        payload = Toolbox(vault).doctor({"level": "strict"})
        assert str(elsewhere) not in str(payload["findings"])


class TestEveryRootRemembersWhatDoctorFound:
    """`details/data-indexing-maintenance.md` section 20, in three assemblies.

    Persisting a finding is one line in each root, which is the shape every
    drift in this file has had. A root that forgot it would still print the
    right findings and still exit with the right code -- and the history would
    simply have a hole where that interface ran.

    So this asserts on the store rather than on the wiring: a broken link, a
    diagnosis through each root, and the same row afterwards.
    """

    @staticmethod
    def _a_broken_link(vault: Path) -> None:
        (vault / "30_Knowledge" / "Notes" / "thing.md").write_text(
            f"---\ntype: knowledge\nid: {ConceptId.new()}\nschema: never4ga/0.1\n"
            'title: Thing\ncreated_at: "2026-08-23T12:00:00Z"\n'
            "---\n\nsee [nothing](nowhere.md)\n",
            encoding="utf-8",
        )

    @staticmethod
    def _open_rules(vault: Path) -> set[str]:
        from contextlib import closing

        from never4ga.adapters.filesystem import FileSystemMarkdownStore
        from never4ga.adapters.sqlite import SQLiteMaintenanceFindings, open_index
        from never4ga.layout import SYSTEM_MANIFEST
        from never4ga.platform_paths import PlatformPaths

        manifest = FileSystemMarkdownStore(vault).get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        database = PlatformPaths.resolve().index_database(manifest.concept_id)
        with closing(open_index(database)) as connection:
            return {f.rule for f in SQLiteMaintenanceFindings(connection).open_findings()}

    def test_the_service_root_records_it(self, vault: Path) -> None:
        self._a_broken_link(vault)
        runtime = VaultRuntime(ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0)))
        runtime.start()
        try:
            with runtime.sessions() as session:
                session.diagnose()
        finally:
            runtime.stop()
        assert "broken_link" in self._open_rules(vault)

    def test_the_cli_root_records_it(self, vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._a_broken_link(vault)
        main(["--vault", str(vault), "index"])
        main(["--vault", str(vault), "--local", "--json", "doctor"])
        capsys.readouterr()
        assert "broken_link" in self._open_rules(vault)

    def test_the_mcp_assembly_records_it(
        self, vault: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from never4ga.mcp.toolbox import Toolbox

        self._a_broken_link(vault)
        # MCP never creates a database (core/05 section 7), so one has to exist.
        main(["--vault", str(vault), "index"])
        capsys.readouterr()
        Toolbox(vault, local=True).doctor({"level": "strict"})
        assert "broken_link" in self._open_rules(vault)


class TestEveryRootNamesARetiredSetting:
    """`doctor` names a retired config setting, from all three roots.

    `doctor` cannot read the machine config itself; only a composition root
    may, so each of the three passes it in. A root that forgot would report one
    warning fewer and still exit zero.

    The config is pointed at a temporary file rather than the real one, so this
    says nothing about the machine it runs on.
    """

    @pytest.fixture
    def retired_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        config = tmp_path / "config.toml"
        config.write_text('[embedding]\nhost = "http://192.0.2.10:11434"\n')
        monkeypatch.setattr(
            "never4ga.config.LocalConfig.default_path", classmethod(lambda cls, paths=None: config)
        )
        return config

    def test_the_service_root_reports_it(self, vault: Path, retired_config: Path) -> None:
        runtime = VaultRuntime(ServiceSettings(vault=vault, endpoint=ServiceEndpoint(port=0)))
        runtime.start()
        try:
            with runtime.sessions() as session:
                codes = [f.code for f in session.doctor().diagnose().findings]
        finally:
            runtime.stop()
        assert "config_names_a_retired_setting" in codes

    def test_the_cli_root_reports_it(
        self, vault: Path, retired_config: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["--vault", str(vault), "--local", "--json", "doctor"])
        assert "config_names_a_retired_setting" in capsys.readouterr().out

    def test_the_mcp_assembly_reports_it(self, vault: Path, retired_config: Path) -> None:
        from never4ga.mcp.toolbox import Toolbox

        payload = Toolbox(vault).doctor({"level": "strict"})
        codes = [finding["code"] for finding in payload["findings"]]
        assert "config_names_a_retired_setting" in codes


class TestEveryRootRefusesAStaleServiceBuild:
    """A daemon runs the code it was started with, and answers anyway.

    Every root must decline to delegate across a build mismatch. A root that
    did not would hand work to whatever build the unit happened to hold, and an
    agent would believe answers from code older than the code it is running.

    The decision lives once, in `composition.service_client`. These tests
    assert the behaviour, and the class below asserts there is only one copy of
    it. Either could be true without the other.
    """

    @staticmethod
    def _health(vault: Path, build: str) -> dict[str, object]:
        manifest = FileSystemMarkdownStore(vault).get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        return {"status": "ok", "vault_id": str(manifest.concept_id), "build": build}

    def test_the_cli_root_declines(self, vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from never4ga import composition

        monkeypatch.setattr(composition, "probe", lambda _url: self._health(vault, "0" * 12))

        from never4ga import cli

        assert cli._connect(vault, local=False) is None

    def test_the_mcp_assembly_declines(self, vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from never4ga import composition
        from never4ga.mcp import toolbox

        monkeypatch.setattr(composition, "probe", lambda _url: self._health(vault, "0" * 12))

        assert toolbox.Toolbox(vault)._client() is None

    def test_the_cli_root_still_delegates_to_a_matching_build(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control. A root that never delegates would pass the two above."""
        from never4ga import composition

        monkeypatch.setattr(
            composition, "probe", lambda _url: self._health(vault, STARTUP_BUILD_ID)
        )

        from never4ga import cli

        assert cli._connect(vault, local=False) is not None

    def test_the_mcp_assembly_still_delegates_to_a_matching_build(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from never4ga import composition
        from never4ga.mcp import toolbox

        monkeypatch.setattr(
            composition, "probe", lambda _url: self._health(vault, STARTUP_BUILD_ID)
        )

        assert toolbox.Toolbox(vault)._client() is not None


class TestTheRouteDecisionIsAssembledOnce:
    """The decision to delegate to a service is written once, not per root.

    MCP makes the same route decision per tool call and may not import another
    composition root, so the choice lives once, in `composition.service_client`.

    A parity test that only *compares* two copies leaves two copies standing.
    This one asserts there is one, by reading each root's routing function:
    the function that decides, not the module, because `service status`
    legitimately probes and compares builds in order to *report* them.
    Reporting health and deciding where work goes are different jobs.
    """

    @staticmethod
    def _routing_source(dotted: str) -> str:
        import importlib
        import inspect

        module_name, _, attribute = dotted.rpartition(".")
        owner = importlib.import_module(module_name)
        for part in attribute.split(":"):
            owner = getattr(owner, part)
        return inspect.getsource(owner)

    ROUTERS = ("never4ga.cli._connect", "never4ga.mcp.toolbox.Toolbox:_client")

    @pytest.mark.parametrize("router", ROUTERS)
    def test_no_root_decides_for_itself(self, router: str) -> None:
        source = self._routing_source(router)
        assert "probe(" not in source, (
            f"{router} probes for itself; the four conditions belong in composition.service_client"
        )
        assert "build_matches(" not in source

    @pytest.mark.parametrize("router", ROUTERS)
    def test_every_root_asks_composition(self, router: str) -> None:
        """The control: not probing is also what a root that never routes does."""
        assert "service_client(" in self._routing_source(router)
