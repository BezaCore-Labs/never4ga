"""Both VaultFileStore implementations against the one contract (core/06 section 22)."""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.fakes import InMemoryVaultFileStore
from never4ga.adapters.filesystem import FileSystemVaultFileStore
from never4ga.errors import VaultPathError
from never4ga.ports.vault_files import VaultFileStore
from tests.contracts.vault_file_store_contract import VaultFileStoreContract, p

pytestmark = pytest.mark.contract


class TestInMemoryVaultFileStore(VaultFileStoreContract):
    @pytest.fixture
    def files(self) -> VaultFileStore:
        return InMemoryVaultFileStore()


class TestFileSystemVaultFileStore(VaultFileStoreContract):
    @pytest.fixture
    def files(self, tmp_path: Path) -> VaultFileStore:
        return FileSystemVaultFileStore(tmp_path)


class TestBothSatisfyThePort:
    def test_the_fake_satisfies_the_protocol(self) -> None:
        assert isinstance(InMemoryVaultFileStore(), VaultFileStore)

    def test_the_filesystem_store_satisfies_the_protocol(self, tmp_path: Path) -> None:
        assert isinstance(FileSystemVaultFileStore(tmp_path), VaultFileStore)


class TestFilesystemBoundary:
    def test_the_root_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(VaultPathError, match="does not exist"):
            FileSystemVaultFileStore(tmp_path / "nope")

    def test_the_root_must_be_a_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "file"
        target.write_text("x")
        with pytest.raises(VaultPathError, match="directory"):
            FileSystemVaultFileStore(target)

    def test_a_symlinked_directory_is_not_walked(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        (outside / "secret.md").write_text("secret")
        (tmp_path / "30_Knowledge").symlink_to(outside, target_is_directory=True)

        files = FileSystemVaultFileStore(tmp_path)
        assert list(files.iter_paths()) == []

    def test_dot_directories_are_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".obsidian").mkdir()
        (tmp_path / ".obsidian" / "app.json").write_text("{}")
        files = FileSystemVaultFileStore(tmp_path)
        assert list(files.iter_paths()) == []
        assert list(files.iter_directories()) == []


class TestFakeAndRealAgree:
    def test_both_report_an_empty_vault_the_same_way(self, tmp_path: Path) -> None:
        for store in (InMemoryVaultFileStore(), FileSystemVaultFileStore(tmp_path)):
            assert list(store.iter_paths()) == []
            assert not store.exists(p("home.md"))
