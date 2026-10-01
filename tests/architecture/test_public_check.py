"""The pre-push check reads what a push would publish.

Every test runs against a throwaway repository and a made-up marker. The real
list lives outside this repository, because this file is as public as any
other.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.architecture

REPOSITORY = Path(__file__).resolve().parents[2]
MARKER = "zebra-harbor"
NO_COMMIT = "0" * 40


def _load() -> ModuleType:
    location = REPOSITORY / "scripts" / "public_check.py"
    spec = importlib.util.spec_from_file_location("public_check", location)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load()
    repository = tmp_path / "repo"
    repository.mkdir()
    for arguments in (
        ["init", "-q", "-b", "main"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.test"],
        ["config", "core.hooksPath", "/dev/null"],
    ):
        subprocess.run(["git", *arguments], cwd=repository, check=True)
    monkeypatch.setattr(module, "REPOSITORY", repository)
    return module


def commit(check: ModuleType, name: str, text: str, message: str = "a change") -> str:
    (check.REPOSITORY / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=check.REPOSITORY, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=check.REPOSITORY, check=True)
    return str(check._git("rev-parse", "HEAD").strip())


def wheres(findings: list[object]) -> list[str]:
    return [str(getattr(finding, "where", "")) for finding in findings]


class TestTheList:
    def test_is_read_from_the_named_file_ignoring_comments(self, tmp_path: Path) -> None:
        listed = tmp_path / "markers.txt"
        listed.write_text(f"# a comment\n\n{MARKER}\n", encoding="utf-8")
        markers = _load().load_markers({"NEVER4GA_PRIVATE_MARKERS": str(listed), "PATH": ""})
        assert markers == (MARKER,)

    def test_a_named_file_that_is_missing_is_an_error(self, tmp_path: Path) -> None:
        module = _load()
        with pytest.raises(module.MarkersMissingError):
            module.load_markers({"NEVER4GA_PRIVATE_MARKERS": str(tmp_path / "gone.txt")})

    def test_no_vault_means_no_list(self) -> None:
        assert _load().load_markers({"PATH": ""}) is None


class TestAPush:
    def test_a_marker_in_an_added_line_is_found(self, check: ModuleType) -> None:
        first = commit(check, "a.txt", "plain\n")
        second = commit(check, "a.txt", f"plain\n{MARKER}\n")
        found = check.check_push([f"refs/heads/main {second} refs/heads/main {first}"], [MARKER])
        assert wheres(found) == [f"{second[:7]} added lines"]

    def test_a_marker_in_a_message_is_found(self, check: ModuleType) -> None:
        first = commit(check, "a.txt", "plain\n")
        second = commit(check, "b.txt", "plain\n", message=f"mentions {MARKER.upper()}")
        found = check.check_push([f"refs/heads/main {second} refs/heads/main {first}"], [MARKER])
        assert wheres(found) == [f"{second[:7]} message"]

    def test_a_marker_removed_later_in_the_push_was_still_published(
        self, check: ModuleType
    ) -> None:
        first = commit(check, "a.txt", "plain\n")
        added = commit(check, "a.txt", f"{MARKER}\n")
        removed = commit(check, "a.txt", "plain\n")
        found = check.check_push([f"refs/heads/main {removed} refs/heads/main {first}"], [MARKER])
        assert wheres(found) == [f"{added[:7]} added lines"]

    def test_a_new_branch_is_checked_whole(self, check: ModuleType) -> None:
        only = commit(check, "a.txt", f"{MARKER}\n")
        found = check.check_push(
            [f"refs/heads/topic {only} refs/heads/topic {NO_COMMIT}"], [MARKER]
        )
        assert wheres(found) == [f"{only[:7]} added lines"]

    def test_a_branch_name_is_published_too(self, check: ModuleType) -> None:
        only = commit(check, "a.txt", "plain\n")
        branch = f"refs/heads/{MARKER}"
        found = check.check_push([f"{branch} {only} {branch} {NO_COMMIT}"], [MARKER])
        assert wheres(found) == ["branch name"]

    def test_a_clean_push_passes(self, check: ModuleType) -> None:
        first = commit(check, "a.txt", "plain\n")
        second = commit(check, "a.txt", "still plain\n")
        assert (
            check.check_push([f"refs/heads/main {second} refs/heads/main {first}"], [MARKER]) == []
        )

    def test_deleting_a_branch_publishes_nothing(self, check: ModuleType) -> None:
        only = commit(check, "a.txt", "plain\n")
        found = check.check_push([f"(delete) {NO_COMMIT} refs/heads/topic {only}"], [MARKER])
        assert found == []


class TestInternalReferences:
    @pytest.mark.parametrize(
        "source",
        [
            "# Decided in ADR-0043.\nx = 1\n",
            '"""Module.\n\nSee Q-031 for why."""\n',
            'def f() -> None:\n    """Closes work item 1281."""\n',
            "# Built in Milestone 10a.\nx = 1\n",
            "# The vault is read once (1167).\nx = 1\n",
        ],
    )
    def test_a_citation_in_a_comment_or_docstring_is_found(self, source: str) -> None:
        assert len(_load().internal_references(source, "a.py")) == 1

    @pytest.mark.parametrize(
        "source",
        [
            '"""Asking for ``ADR-0002`` must not return ``ADR-00021``."""\n',
            "# A title such as `ADR-0001 — Use SQLite` is an identifier.\nx = 1\n",
            "# core/02 section 21.11 states the rule.\nx = 1\n",
            'TITLE = "ADR-0033 — Next"  # test data is not prose\n',
        ],
    )
    def test_an_example_a_spec_citation_or_data_passes(self, source: str) -> None:
        assert _load().internal_references(source, "a.py") == []

    def test_a_push_is_checked_on_the_python_files_it_changes(self, check: ModuleType) -> None:
        (check.REPOSITORY / "src").mkdir()
        first = commit(check, "src/a.py", "x = 1\n")
        second = commit(check, "src/a.py", "# See ADR-0043.\nx = 1\n")
        found = check.check_push_prose([f"refs/heads/main {second} refs/heads/main {first}"])
        assert [str(finding) for finding in found] == ["src/a.py:1: 'ADR-0043'"]

    @pytest.mark.parametrize(
        ("name", "source"),
        [
            ("pyproject.toml", "# Pulled in under ADR-0013.\nname = 'x'\n"),
            (".github/workflows/gate.yml", "on:\n  push:  # cancelled runs (1121)\n"),
            (".githooks/pre-push", "#!/bin/sh\n# Required by ADR-0047.\nexit 0\n"),
            ("scripts/run.sh", "#!/bin/sh\n  # See Q-031.\n"),
            (".github/workflows/gate.yml", "run: echo it's done\n# See ADR-0043.\n"),
        ],
    )
    def test_a_citation_in_a_hash_comment_is_found(self, name: str, source: str) -> None:
        assert len(_load().internal_references(source, name)) == 1

    @pytest.mark.parametrize(
        ("name", "source"),
        [
            ("pyproject.toml", 'notes = "see ADR-0013 # and Q-031"\n'),
            ("a.toml", "# Quoted as `ADR-0025` in the marker.\n"),
            (".github/workflows/gate.yml", "# core/05 section 3 states the rule.\n"),
        ],
    )
    def test_data_examples_and_spec_citations_in_hash_files_pass(
        self, name: str, source: str
    ) -> None:
        assert _load().internal_references(source, name) == []

    def test_a_push_is_checked_on_the_hash_commented_files_it_changes(
        self, check: ModuleType
    ) -> None:
        first = commit(check, "pyproject.toml", "name = 'x'\n")
        second = commit(check, "pyproject.toml", "# See ADR-0043.\nname = 'x'\n")
        found = check.check_push_prose([f"refs/heads/main {second} refs/heads/main {first}"])
        assert [str(finding) for finding in found] == ["pyproject.toml:1: 'ADR-0043'"]

    def test_files_outside_the_held_roots_are_not_checked(self, check: ModuleType) -> None:
        first = commit(check, "notes.py", "x = 1\n")
        second = commit(check, "notes.py", "# See ADR-0043.\nx = 1\n")
        assert check.check_push_prose([f"refs/heads/main {second} refs/heads/main {first}"]) == []
