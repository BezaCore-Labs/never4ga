"""WorkspaceMappingStore contract.

core/06 section 22: every backend interface gets one reusable suite, and an
implementation becomes supported by passing it.

The mappings this store holds are machine-local state Never4gA *writes*. They
are durable rather than derived: deleting them loses knowledge the vault does not have, so
"round-trips unchanged" is the property that matters most here.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.domain.scope import WorkspaceMapping
from never4ga.ports.workspace_mappings import WorkspaceMappingStore

PARENT = ConceptId.new()
CHILD = ConceptId.new()


def mapping(
    workspace_id: ConceptId = PARENT,
    *,
    path: str = "10_Workspaces/BezaCore-Labs/workspace.md",
    repository_root: str | None = "/home/user/Projects/never4ga",
    parent_id: ConceptId | None = None,
) -> WorkspaceMapping:
    return WorkspaceMapping(
        workspace_id=workspace_id,
        workspace_path=VaultPath.parse(path),
        repository_root=None if repository_root is None else PurePosixPath(repository_root),
        parent_id=parent_id,
    )


class WorkspaceMappingStoreContract:
    @pytest.fixture
    def store(self) -> WorkspaceMappingStore:
        raise NotImplementedError("supply a WorkspaceMappingStore fixture")

    def test_an_unwritten_store_is_empty(self, store: WorkspaceMappingStore) -> None:
        assert tuple(store.load()) == ()

    def test_save_then_load_round_trips(self, store: WorkspaceMappingStore) -> None:
        store.save([mapping()])
        loaded = tuple(store.load())
        assert loaded == (mapping(),)

    def test_every_field_survives_the_round_trip(self, store: WorkspaceMappingStore) -> None:
        original = mapping(
            CHILD,
            path="10_Workspaces/BezaCore-Labs/Workspaces/never4ga/workspace.md",
            repository_root="/home/user/Projects/never4ga",
            parent_id=PARENT,
        )
        store.save([original])
        (loaded,) = store.load()
        assert loaded.workspace_id == original.workspace_id
        assert loaded.workspace_path == original.workspace_path
        assert loaded.repository_root == original.repository_root
        assert loaded.parent_id == original.parent_id

    def test_a_workspace_without_a_repository_round_trips(
        self, store: WorkspaceMappingStore
    ) -> None:
        # A workspace need not have a repository at all; it stays addressable by id.
        store.save([mapping(repository_root=None)])
        (loaded,) = store.load()
        assert loaded.repository_root is None

    def test_save_replaces_the_whole_set(self, store: WorkspaceMappingStore) -> None:
        store.save([mapping(PARENT), mapping(CHILD, parent_id=PARENT)])
        store.save([mapping(CHILD, parent_id=PARENT)])
        assert tuple(m.workspace_id for m in store.load()) == (CHILD,)

    def test_saving_nothing_empties_the_store(self, store: WorkspaceMappingStore) -> None:
        store.save([mapping()])
        store.save([])
        assert tuple(store.load()) == ()

    def test_load_order_is_deterministic(self, store: WorkspaceMappingStore) -> None:
        store.save([mapping(CHILD, parent_id=PARENT), mapping(PARENT)])
        assert tuple(store.load()) == tuple(store.load())
