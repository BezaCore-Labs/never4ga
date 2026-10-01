"""Derived state lives outside the vault, at platform-appropriate paths.

core/05 section 7 requires runtime and derived state to live outside the
Git-backed vault, "obtained through a platform abstraction". It requires the
abstraction, not a library, so XDG resolution is done with the standard library.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.domain.identity import ConceptId
from never4ga.platform_paths import PlatformPaths


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


class TestDefaults:
    def test_the_xdg_defaults_are_used_when_nothing_is_set(self, home: Path) -> None:
        paths = PlatformPaths.resolve(environment={}, home=home)
        assert paths.config == home / ".config" / "never4ga"
        assert paths.data == home / ".local" / "share" / "never4ga"
        assert paths.state == home / ".local" / "state" / "never4ga"
        assert paths.cache == home / ".cache" / "never4ga"

    def test_every_directory_is_namespaced_to_never4ga(self, home: Path) -> None:
        paths = PlatformPaths.resolve(environment={}, home=home)
        assert all(
            directory.name == "never4ga"
            for directory in (paths.config, paths.data, paths.state, paths.cache)
        )


class TestEnvironment:
    def test_each_variable_is_honoured(self, tmp_path: Path, home: Path) -> None:
        paths = PlatformPaths.resolve(
            environment={
                "XDG_CONFIG_HOME": str(tmp_path / "c"),
                "XDG_DATA_HOME": str(tmp_path / "d"),
                "XDG_STATE_HOME": str(tmp_path / "s"),
                "XDG_CACHE_HOME": str(tmp_path / "k"),
            },
            home=home,
        )
        assert paths.config == tmp_path / "c" / "never4ga"
        assert paths.data == tmp_path / "d" / "never4ga"
        assert paths.state == tmp_path / "s" / "never4ga"
        assert paths.cache == tmp_path / "k" / "never4ga"

    @pytest.mark.parametrize("value", ["", "   ", "relative/path", "./relative"])
    def test_a_value_that_is_not_an_absolute_path_is_ignored(self, home: Path, value: str) -> None:
        # The XDG base directory specification: "If an implementation encounters
        # a relative path, it must be considered invalid and ignored."
        paths = PlatformPaths.resolve(environment={"XDG_DATA_HOME": value}, home=home)
        assert paths.data == home / ".local" / "share" / "never4ga"

    def test_the_real_environment_is_used_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "/somewhere/absolute")
        assert PlatformPaths.resolve().data == Path("/somewhere/absolute/never4ga")


class TestPerVaultLayout:
    def test_the_index_database_follows_the_core_05_section_8_layout(self, home: Path) -> None:
        vault_id = ConceptId.new()
        paths = PlatformPaths.resolve(environment={}, home=home)
        assert paths.index_database(vault_id) == (
            home / ".local/share/never4ga/vaults" / str(vault_id) / "index.sqlite3"
        )

    def test_the_tracker_cache_sits_beside_the_index_in_its_own_file(self, home: Path) -> None:
        # Beside the index, not inside it, so `rebuild` never has to learn
        # which tables to spare.
        vault_id = ConceptId.new()
        paths = PlatformPaths.resolve(environment={}, home=home)
        assert paths.trackers_database(vault_id) == (
            home / ".local/share/never4ga/vaults" / str(vault_id) / "trackers.sqlite3"
        )
        assert paths.trackers_database(vault_id) != paths.index_database(vault_id)

    def test_each_vault_gets_its_own_directory(self, home: Path) -> None:
        paths = PlatformPaths.resolve(environment={}, home=home)
        first, second = ConceptId.new(), ConceptId.new()
        assert paths.vault_directory(first) != paths.vault_directory(second)

    def test_derived_material_sits_beside_the_database(self, home: Path) -> None:
        vault_id = ConceptId.new()
        paths = PlatformPaths.resolve(environment={}, home=home)
        assert paths.derived_directory(vault_id).parent == paths.vault_directory(vault_id)

    def test_resolving_paths_touches_no_filesystem(self, home: Path) -> None:
        vault_id = ConceptId.new()
        paths = PlatformPaths.resolve(environment={}, home=home)
        paths.index_database(vault_id)
        # Nothing is created merely by asking where something belongs; the
        # component that writes is the component that creates.
        assert not home.exists()


class TestMachineLocalState:
    """Files Never4gA writes for itself outside any one vault."""

    def test_the_workspace_mappings_are_per_vault(self, home: Path) -> None:
        # A machine-wide file would be read by every vault on the machine, so
        # a repository mapped from a scratch vault would resolve from another.
        # Durable like the session store, so it sits beside it.
        paths = PlatformPaths.resolve(environment={}, home=home)
        vault_id = ConceptId.new()
        assert paths.workspaces_file(vault_id) == paths.vault_directory(vault_id) / (
            "workspaces.json"
        )

    def test_two_vaults_never_share_a_mapping_file(self, home: Path) -> None:
        paths = PlatformPaths.resolve(environment={}, home=home)
        assert paths.workspaces_file(ConceptId.new()) != paths.workspaces_file(ConceptId.new())

    def test_the_machine_wide_file_is_where_it_always_was(self, home: Path) -> None:
        # Kept by name so mappings in the older machine-wide file can be
        # adopted.
        paths = PlatformPaths.resolve(environment={}, home=home)
        assert paths.machine_workspaces_file == home / ".local/state/never4ga/workspaces.json"
        assert paths.machine_workspaces_file.parent == paths.secrets_file.parent

    def test_asking_where_the_mappings_belong_creates_nothing(self, home: Path) -> None:
        where = PlatformPaths.resolve(environment={}, home=home).workspaces_file(ConceptId.new())
        assert where.name == "workspaces.json"
        assert not home.exists()
