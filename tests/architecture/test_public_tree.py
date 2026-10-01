"""What this public repository carries names nothing private.

Two kinds of check:

- **The maintainer's name**, which is public already through the package
  metadata, is kept out of everything else. Shipped code runs in other
  people's vaults, and a Skill that addresses the maintainer by name is
  instructions about a stranger there.
- **Everything else private** is listed in a file kept outside this
  repository, and checked only where that file exists. Listing the markers
  here would publish exactly what they keep out. `scripts/public_check.py`
  reads the same file before every push.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import never4ga

pytestmark = pytest.mark.architecture

SOURCE = Path(never4ga.__file__).parent
REPOSITORY = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve().relative_to(REPOSITORY).as_posix()

#: Public already, through `pyproject.toml`'s authors; kept out of the rest.
NAME = "Joseph"
NAME_ALLOWED: frozenset[str] = frozenset({"pyproject.toml", THIS_FILE})


#: The environment the run started in. `tests/conftest.py` gives every test a
#: throwaway HOME, which hides the vault; reading the private list only reads.
_STARTING_ENVIRONMENT = dict(os.environ)


def _tracked() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPOSITORY, capture_output=True, check=True
    ).stdout.decode()
    return [name for name in listed.split("\0") if name]


def _text(relative: str) -> str | None:
    try:
        return (REPOSITORY / relative).read_text(encoding="utf-8")
    except UnicodeDecodeError, FileNotFoundError:
        return None


def _public_check() -> ModuleType:
    location = REPOSITORY / "scripts" / "public_check.py"
    spec = importlib.util.spec_from_file_location("public_check", location)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs, because a dataclass looks its module up.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_shipped_file_names_the_maintainer() -> None:
    offending = [
        str(path.relative_to(SOURCE))
        for path in sorted(SOURCE.rglob("*.py"))
        if NAME.casefold() in path.read_text(encoding="utf-8").casefold()
    ]
    assert offending == []


def test_no_tracked_file_names_the_maintainer() -> None:
    offending = [
        relative
        for relative in _tracked()
        if relative not in NAME_ALLOWED and NAME.casefold() in (_text(relative) or "").casefold()
    ]
    assert offending == []


def test_no_tracked_file_carries_a_private_marker() -> None:
    check = _public_check()
    markers = check.load_markers(_STARTING_ENVIRONMENT)
    if markers is None:
        pytest.skip("the private list is in the maintainer's vault, which is not here")
    # Name the file, never the marker: this output can end up in a public CI log.
    offending = sorted({finding.where for finding in check.check_tree(markers)})
    assert offending == [], "a tracked file carries a private marker; run scripts/public_check.py"


def test_no_comment_cites_a_record_a_reader_cannot_open() -> None:
    # Needs no vault: the rule is public, and so is everything it reads.
    offending = [str(finding) for finding in _public_check().check_tree_prose()]
    assert offending == [], "cite a public spec section, or explain the rule itself"
