"""One adapter diagnosis reads the vault's Skills once.

`diagnose` compares every installed client against the vault's Skills. That
list belongs to the vault, not the client, and cannot change between clients,
so it is read once and handed down rather than once per installed client.

The tests count reads rather than time them, for the reason
`test_diagnosis_reads_the_vault_once.py` gives: a timing test measures the
machine, and an extra read would be silent, because every finding would still
be right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.filesystem import (
    DeploymentFile,
    FileSystemClientFileStore,
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
)
from never4ga.domain.clients import ClientDescriptor
from never4ga.services import VaultInitializer
from never4ga.services import adapters as adapters_module
from never4ga.services.adapters import AdapterService


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir(exist_ok=True)
    return root


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Test Vault"
    )
    return root


def descriptor(client_id: str) -> ClientDescriptor:
    return ClientDescriptor(
        client_id=client_id,
        title=client_id.title(),
        global_skills_path=f"~/.{client_id}/skills",
        detect_paths=(f"~/.{client_id}",),
    )


def service(vault: Path, home: Path, tmp_path: Path, clients: int) -> AdapterService:
    descriptors = []
    for index in range(clients):
        name = f"probe{index}"
        (home / f".{name}").mkdir()  # installed, so the loop body runs
        descriptors.append(descriptor(name))
    return AdapterService(
        descriptors,
        FileSystemVaultFileStore(vault),
        DeploymentFile(tmp_path / f"state{clients}" / "deployments.json"),
        FileSystemClientFileStore(),
        home=home,
        vault_root=vault,
    )


def reads(
    monkeypatch: pytest.MonkeyPatch, built: AdapterService, *, name: str = "canonical_skills"
) -> int:
    """How many times one `diagnose()` reads the vault's Skills."""
    counted = {"n": 0}
    original = getattr(adapters_module, name)

    def wrapper(files: object) -> object:
        counted["n"] += 1
        return original(files)

    monkeypatch.setattr(adapters_module, name, wrapper)
    built.diagnose()
    return counted["n"]


class TestTheSkillsAreReadOnce:
    def test_one_installed_client_reads_them_once(
        self, vault: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert reads(monkeypatch, service(vault, home, tmp_path, 1)) == 1

    def test_four_installed_clients_still_read_them_once(
        self, vault: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The cost of a diagnosis must not scale with how many agent clients
        # are installed on the machine.
        assert reads(monkeypatch, service(vault, home, tmp_path, 4)) == 1

    def test_no_installed_client_reads_them_at_all(
        self, vault: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing to compare against, so nothing to read. Hoisting the call out
        # of the loop must not turn it into work a machine with no clients pays.
        built = AdapterService(
            [descriptor("absent")],
            FileSystemVaultFileStore(vault),
            DeploymentFile(tmp_path / "state-none" / "deployments.json"),
            FileSystemClientFileStore(),
            home=home,
            vault_root=vault,
        )
        assert reads(monkeypatch, built) == 0


class TestASessionPaysOnlyWhenAsked:
    """A session runs the adapter diagnosis only when `doctor` asks for it.

    The API opens a session per request, so a diagnosis built eagerly would
    make every health poll, search and context pack pay for a question only
    `doctor` asks. The session carries the means to answer, not the answer.
    """

    def test_a_session_that_never_diagnoses_never_asks(self) -> None:
        from never4ga.adapters.fakes import (
            InMemoryDocumentStore,
            InMemoryGraphIndex,
            InMemoryIndexState,
            InMemoryMetadataIndex,
            InMemoryTextIndex,
            InMemoryVaultFileStore,
        )
        from never4ga.domain.identity import ConceptId
        from never4ga.services.session import VaultSession

        asked = {"n": 0}

        def diagnosis() -> tuple[object, ...]:
            asked["n"] += 1
            return ()

        session = VaultSession(
            vault_id=ConceptId.new(),
            files=InMemoryVaultFileStore(),
            documents=InMemoryDocumentStore(),
            metadata=InMemoryMetadataIndex(),
            text=InMemoryTextIndex(),
            graph=InMemoryGraphIndex(),
            state=InMemoryIndexState(),
            adapters=diagnosis,  # type: ignore[arg-type]
        )
        # Everything a request does that is not `doctor`.
        session.indexer.health()
        assert session.searcher is not None
        assert asked["n"] == 0

        session.doctor()
        assert asked["n"] == 1
