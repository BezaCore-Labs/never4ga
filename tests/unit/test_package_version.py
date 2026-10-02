"""The version is written once, in `pyproject.toml`.

Everything that reports a version reads it from the installed package, so a
release cannot ship code that describes itself as an older one.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

from never4ga import __version__

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_the_package_reports_the_version_it_was_installed_as() -> None:
    assert __version__ == version("never4ga")


def test_the_installed_version_is_the_one_pyproject_declares() -> None:
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert version("never4ga") == declared
