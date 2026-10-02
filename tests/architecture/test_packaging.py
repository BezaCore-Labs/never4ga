"""What a release publishes, and how it is published.

The source distribution lists what it may contain. Left to the build tool,
it takes every file the repository's `.gitignore` does not exclude, and a
file kept out of Git only by a machine's global ignore rules -- a local
settings file, say -- is published with the release.

A release is uploaded with trusted publishing: the package index trusts this
repository's workflow, so no upload token is stored anywhere.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def tracked(path: str) -> bool:
    listed = subprocess.run(
        ["git", "ls-files", "--", path], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return bool(listed.strip())


class TestTheSourceDistribution:
    def only_include(self) -> list[str]:
        targets = PYPROJECT["tool"]["hatch"]["build"]["targets"]
        return list(targets["sdist"]["only-include"])

    def test_it_names_what_it_ships(self) -> None:
        assert self.only_include()

    def test_everything_it_names_is_tracked(self) -> None:
        assert [path for path in self.only_include() if not tracked(path)] == []

    def test_it_names_nothing_hidden(self) -> None:
        assert [path for path in self.only_include() if path.startswith(".")] == []

    def test_it_carries_the_package_its_readme_and_its_licence(self) -> None:
        assert {"src/never4ga", "README.md", "LICENSE", "pyproject.toml"} <= set(
            self.only_include()
        )


class TestTheMetadata:
    def test_it_links_back_to_the_repository(self) -> None:
        urls = PYPROJECT["project"]["urls"]
        assert urls["Repository"] == "https://github.com/BezaCore-Labs/never4ga"

    def test_it_says_which_python_it_needs(self) -> None:
        assert "Programming Language :: Python :: 3.14" in PYPROJECT["project"]["classifiers"]


class TestTheReleaseWorkflow:
    def text(self) -> str:
        return RELEASE.read_text(encoding="utf-8")

    def test_it_uses_trusted_publishing_and_no_stored_token(self) -> None:
        assert "id-token: write" in self.text()
        assert "${{ secrets" not in self.text()
        assert "password:" not in self.text()

    def test_it_checks_the_tag_against_the_version_before_publishing(self) -> None:
        assert "does not match the package version" in self.text()

    def test_the_index_is_test_pypi_unless_a_release_tag_was_pushed(self) -> None:
        assert "https://test.pypi.org/legacy/" in self.text()
        assert "refs/tags/v" in self.text()
