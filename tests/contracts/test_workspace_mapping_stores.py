"""Every WorkspaceMappingStore implementation against the one contract.

The in-memory fake keeps service tests off the filesystem; the file store is
what a machine actually uses. Both pass the same suite, which is the point of
core/06 section 22.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.fakes import InMemoryWorkspaceMappingStore
from never4ga.adapters.filesystem import VaultWorkspaceMappings, WorkspaceMappingFile
from never4ga.domain.scope import WorkspaceMapping
from never4ga.errors import WorkspaceMappingStoreError
from never4ga.ports.workspace_mappings import WorkspaceMappingStore
from tests.contracts.workspace_mapping_store_contract import (
    CHILD,
    PARENT,
    WorkspaceMappingStoreContract,
    mapping,
)

pytestmark = pytest.mark.contract


class TestInMemoryWorkspaceMappingStore(WorkspaceMappingStoreContract):
    @pytest.fixture
    def store(self) -> WorkspaceMappingStore:
        return InMemoryWorkspaceMappingStore()


class TestWorkspaceMappingFile(WorkspaceMappingStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> WorkspaceMappingStore:
        return WorkspaceMappingFile(tmp_path / "never4ga" / "workspaces.json")


class TestVaultWorkspaceMappings(WorkspaceMappingStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> WorkspaceMappingStore:
        return VaultWorkspaceMappings(
            tmp_path / "vault" / "workspaces.json",
            machine=tmp_path / "state" / "workspaces.json",
            claims=lambda _: False,
        )


class TestAdoptingTheMachineWideFile:
    """Mappings in the older machine-wide file, and which vault gets them.

    Older builds kept one file per machine, read by every vault on it. The
    entries do not say which vault they came from, so a vault claims only the
    entries whose workspace it demonstrably holds -- the rule is the ``claims``
    predicate, and composition supplies it. Reading claims nothing on disk; the
    first save moves the claimed entries across.
    """

    OURS = mapping(PARENT, repository_root="/work/ours")
    THEIRS = mapping(CHILD, path="10_Workspaces/Baking/workspace.md", repository_root="/tmp/b")

    @pytest.fixture
    def machine(self, tmp_path: Path) -> Path:
        path = tmp_path / "state" / "workspaces.json"
        WorkspaceMappingFile(path).save([self.OURS, self.THEIRS])
        return path

    @pytest.fixture
    def own(self, tmp_path: Path) -> Path:
        return tmp_path / "vault" / "workspaces.json"

    def _store(self, own: Path, machine: Path) -> VaultWorkspaceMappings:
        return VaultWorkspaceMappings(
            own, machine=machine, claims=lambda m: m.workspace_id == PARENT
        )

    def test_a_vault_reads_the_entries_it_claims(self, own: Path, machine: Path) -> None:
        assert tuple(self._store(own, machine).load()) == (self.OURS,)

    def test_reading_moves_nothing(self, own: Path, machine: Path) -> None:
        before = machine.read_text()
        self._store(own, machine).load()
        assert not own.exists()
        assert machine.read_text() == before

    def test_saving_takes_the_claimed_entries_out_of_the_machine_file(
        self, own: Path, machine: Path
    ) -> None:
        store = self._store(own, machine)
        store.save(store.load())
        assert tuple(WorkspaceMappingFile(own).load()) == (self.OURS,)
        assert tuple(WorkspaceMappingFile(machine).load()) == (self.THEIRS,)

    def test_the_machine_file_goes_once_nothing_is_left_in_it(
        self, own: Path, tmp_path: Path
    ) -> None:
        machine = tmp_path / "state" / "workspaces.json"
        WorkspaceMappingFile(machine).save([self.OURS])
        store = self._store(own, machine)
        store.save(store.load())
        assert not machine.exists()

    def test_an_unmap_is_not_undone_by_the_machine_file(self, own: Path, machine: Path) -> None:
        store = self._store(own, machine)
        store.save([])
        assert tuple(store.load()) == ()

    def test_the_vault_s_own_entry_wins_for_the_same_repository(
        self, own: Path, machine: Path
    ) -> None:
        # An older build still running as the service could write the
        # machine file after the vault has its own. The vault's own is newer.
        corrected = WorkspaceMapping(
            workspace_id=PARENT,
            workspace_path=self.OURS.workspace_path,
            repository_root=self.OURS.repository_root,
            ships_agent_contract=True,
        )
        WorkspaceMappingFile(own).save([corrected])
        assert tuple(self._store(own, machine).load()) == (corrected,)

    def test_no_machine_file_costs_nothing(self, own: Path, tmp_path: Path) -> None:
        calls: list[WorkspaceMapping] = []

        def claims(candidate: WorkspaceMapping) -> bool:
            calls.append(candidate)
            return True

        store = VaultWorkspaceMappings(own, machine=tmp_path / "absent.json", claims=claims)
        store.save([self.OURS])
        assert tuple(store.load()) == (self.OURS,)
        assert calls == []


class TestTheFileItself:
    @pytest.fixture
    def path(self, tmp_path: Path) -> Path:
        return tmp_path / "never4ga" / "workspaces.json"

    def test_the_file_is_not_created_until_something_is_saved(self, path: Path) -> None:
        # core/05 section 7: asking where something belongs must not create it.
        WorkspaceMappingFile(path).load()
        assert not path.exists()

    def test_saving_creates_the_directory(self, path: Path) -> None:
        WorkspaceMappingFile(path).save([mapping()])
        assert path.is_file()

    def test_the_file_is_owner_only(self, path: Path) -> None:
        WorkspaceMappingFile(path).save([mapping()])
        assert path.stat().st_mode & 0o077 == 0

    def test_a_damaged_file_is_refused_rather_than_read_as_empty(self, path: Path) -> None:
        # Reading damage as "no mappings" would destroy it on the next save.
        path.parent.mkdir(parents=True)
        path.write_text("{ this is not json")
        with pytest.raises(WorkspaceMappingStoreError):
            WorkspaceMappingFile(path).load()

    def test_a_file_in_an_unexpected_shape_is_refused(self, path: Path) -> None:
        path.parent.mkdir(parents=True)
        path.write_text('{"version": 1, "workspaces": "not a list"}')
        with pytest.raises(WorkspaceMappingStoreError):
            WorkspaceMappingFile(path).load()

    def test_a_second_store_sees_what_the_first_wrote(self, path: Path) -> None:
        WorkspaceMappingFile(path).save([mapping()])
        assert tuple(WorkspaceMappingFile(path).load()) == (mapping(),)
