"""A diagnosis knows whether it looked at everything.

`details/data-indexing-maintenance.md` section 20 gives a finding a
``resolved_at``, set by re-detection. Re-detection resolves a finding *by not
reporting it*, so a run that never looked for a rule must not be mistaken for a
run that looked and found nothing. Otherwise it would close every finding it
did not ask about, recording "this is fixed" where the truth is "nobody
checked".

:class:`~never4ga.services.doctor.Doctor` holds the optional inputs, so it is
the only thing that knows which happened. The diagnosis carries the answer
rather than each caller re-deriving it.

These make a run partial:

- **a validation level below ``strict``**, which asks for fewer schema rules;
- **no index**, which silently drops every check in ``_check_index`` -- staleness,
  broken links, unresolved relations;
- **no repository locator**, which drops the checks that read a mapped
  repository's agent files;
- **no adapter findings**, which drops what section 24 calls "Agent adapters"
  and "Skill sync".

An empty set of workspace mappings is *not* on that list: no repository being
mapped is a real answer, not a refusal to look.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from never4ga.adapters.fakes import FakeRepositoryLocator
from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.schema import ValidationLevel
from never4ga.services import VaultInitializer
from never4ga.services.doctor import Doctor
from never4ga.services.indexing import IndexHealth


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(FileSystemVaultFileStore(root), FileSystemMarkdownStore(root)).initialize(
        "Test Vault"
    )
    return root


def doctor(vault: Path, **overrides: object) -> Doctor:
    arguments: dict[str, object] = {
        "level": ValidationLevel.STRICT,
        "index": IndexHealth(),
        "repositories": FakeRepositoryLocator(),
        "adapters": (),
    }
    arguments.update(overrides)
    return Doctor(
        FileSystemVaultFileStore(vault),
        FileSystemMarkdownStore(vault),
        **arguments,  # type: ignore[arg-type]
    )


class TestCompleteness:
    def test_a_strict_run_with_every_input_is_complete(self, vault: Path) -> None:
        assert doctor(vault).diagnose().complete

    def test_a_level_below_strict_is_partial(self, vault: Path) -> None:
        assert not doctor(vault, level=ValidationLevel.CORE).diagnose().complete
        assert not doctor(vault, level=ValidationLevel.OKF).diagnose().complete

    def test_no_index_is_partial(self, vault: Path) -> None:
        # `_check_index` produces nothing at all without one, so `index_stale`
        # and every broken link would resolve on a run that could not see them.
        assert not doctor(vault, index=None).diagnose().complete

    def test_no_repository_locator_is_partial(self, vault: Path) -> None:
        assert not doctor(vault, repositories=None).diagnose().complete

    def test_no_adapter_findings_is_partial(self, vault: Path) -> None:
        assert not doctor(vault, adapters=None).diagnose().complete

    def test_but_an_empty_list_of_them_is_not(self, vault: Path) -> None:
        # Nothing drifted is a result, like nothing being mapped.
        assert doctor(vault, adapters=()).diagnose().complete

    def test_no_mapped_repositories_is_still_complete(self, vault: Path) -> None:
        # Nothing mapped is an answer, not an absence: the repository checks ran
        # and had nothing to say. This is the one optional input whose empty
        # value is not a refusal to look.
        assert doctor(vault, mappings=()).diagnose().complete
