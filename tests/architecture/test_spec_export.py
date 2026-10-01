"""docs/specs/ is an exported copy of the specifications, checked by hash.

The specifications are maintained in a Never4gA vault, and
`scripts/export_specs.py` copies its `Architecture/` here byte for byte, with a
manifest of hashes. Two ways it can go wrong, and a
test for each:

- **Edited here.** A file in `docs/specs/` no longer hashes to its manifest
  entry. That runs everywhere, CI included: the edit would be lost at the next
  export, and the published text would differ from what every session reads.
- **Fallen behind.** A specification changed in the vault and nobody exported
  it. That can only be seen where the vault is, so it skips everywhere else.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.architecture

REPOSITORY = Path(__file__).resolve().parents[2]
SPECS = REPOSITORY / "docs" / "specs"
GENERATED = frozenset({"manifest.json", "README.md"})

#: The environment the run started in. `tests/conftest.py` gives every test a
#: throwaway HOME and XDG directories, which hides the vault from the CLI; the
#: staleness check only reads, so it asks in the real one. Captured at import,
#: before any fixture runs.
_STARTING_ENVIRONMENT = dict(os.environ)


def _script() -> ModuleType:
    location = REPOSITORY / "scripts" / "export_specs.py"
    spec = importlib.util.spec_from_file_location("export_specs", location)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict[str, Any]:
    loaded = json.loads((SPECS / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _exported() -> set[str]:
    return {
        path.relative_to(SPECS).as_posix()
        for path in SPECS.rglob("*")
        if path.is_file() and path.relative_to(SPECS).as_posix() not in GENERATED
    }


def test_the_manifest_lists_exactly_what_is_here() -> None:
    assert set(_manifest()["files"]) == _exported()


def test_nothing_here_was_edited_after_export() -> None:
    edited = [
        relative
        for relative, recorded in _manifest()["files"].items()
        if hashlib.sha256((SPECS / relative).read_bytes()).hexdigest() != recorded
    ]
    assert edited == [], "docs/specs/ is generated: edit the vault and re-export"


def test_the_copy_says_it_is_generated() -> None:
    assert "Do not edit" in (SPECS / "README.md").read_text(encoding="utf-8")


def test_the_index_is_there_to_start_from() -> None:
    assert "00-spec-index.md" in _manifest()["files"]


def test_the_export_is_current_with_the_vault() -> None:
    script = _script()
    found = script.find_source(_STARTING_ENVIRONMENT)
    if found is None:
        pytest.skip("the vault is not on this machine; staleness is checked where it is")
    source, _name = found
    assert script.source_files(source) == _manifest()["files"], (
        "a specification changed in the vault: run scripts/export_specs.py"
    )
