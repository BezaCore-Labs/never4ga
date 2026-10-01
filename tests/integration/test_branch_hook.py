"""The pre-commit hook refuses a commit made directly on the default branch.

A rule that lives only in a document gets broken. The usual slip is `git add
-A && git commit` run before remembering to branch.

Work lands through pull requests, which merge a *squashed* version. A commit on
local `main` then holds the same tree under a second commit id, `main` stops
fast-forwarding, and the repair is a force push.

The hook is shell, so it is tested by running it: a throwaway repository, the
real script, and a commit attempted from each side of the rule. Asserting the
file merely exists would test that somebody wrote a file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "pre-commit"


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        # A committer identity the sandbox is guaranteed to have, and no chance
        # of reading the developer's real one.
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".githooks").mkdir(parents=True)
    shutil.copy(HOOK, root / ".githooks" / "pre-commit")
    (root / ".githooks" / "pre-commit").chmod(0o755)

    git("init", "--initial-branch", "main", cwd=root)
    git("config", "core.hooksPath", ".githooks", cwd=root)
    (root / "a.txt").write_text("one\n")
    git("add", "a.txt", cwd=root)
    return root


class TestItRefusesTheDefaultBranch:
    def test_a_commit_on_main_is_refused(self, repository: Path) -> None:
        result = git("commit", "-m", "on main", cwd=repository)

        assert result.returncode != 0
        assert "refusing to commit directly on main" in result.stderr

    def test_it_says_how_to_recover(self, repository: Path) -> None:
        """The message has to carry the fix, or it is just an obstacle."""
        result = git("commit", "-m", "on main", cwd=repository)

        assert "git switch -c" in result.stderr

    def test_nothing_is_committed(self, repository: Path) -> None:
        git("commit", "-m", "on main", cwd=repository)

        log = git("log", "--oneline", cwd=repository)
        assert log.returncode != 0 or log.stdout.strip() == ""


class TestItAllowsEverythingElse:
    def test_a_commit_on_a_branch_succeeds(self, repository: Path) -> None:
        git("switch", "-c", "some-work", cwd=repository)

        result = git("commit", "-m", "on a branch", cwd=repository)

        assert result.returncode == 0, result.stderr

    def test_the_staged_work_survives_the_switch(self, repository: Path) -> None:
        """Which is what makes recovering from the refusal a single command."""
        git("commit", "-m", "on main", cwd=repository)
        git("switch", "-c", "some-work", cwd=repository)

        assert git("commit", "-m", "recovered", cwd=repository).returncode == 0
        assert "a.txt" in git("show", "--name-only", cwd=repository).stdout


class TestItCanBeOverriddenDeliberately:
    def test_no_verify_bypasses_it(self, repository: Path) -> None:
        """Deliberate, not impossible. A hook nobody can escape gets removed."""
        result = git("commit", "--no-verify", "-m", "on main anyway", cwd=repository)

        assert result.returncode == 0, result.stderr


def stub(directory: Path, name: str, *, exit_code: int = 0) -> Path:
    """A fake `ruff` or `mypy` that records how it was called.

    The hook runs whatever it finds; testing it against the real tools would be
    testing ruff. What is worth asserting is the wiring -- which checks run,
    when they are skipped, and whether a failure stops the commit -- so the
    stubs report their arguments and exit however the test needs.
    """
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s %s\\n" "{name}" "$*" >> "$(dirname "$0")/../../calls.log"\n'
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    return script


def calls(repository: Path) -> list[str]:
    log = repository / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def branched(repository: Path) -> Path:
    """The repository on a branch, so the default-branch rule is out of the way."""
    git("switch", "-c", "some-work", cwd=repository)
    git("commit", "-m", "base", cwd=repository)
    return repository


class TestItRunsTheGatesFastChecks:
    """`ruff` and `mypy` cost half a second and catch two whole failure classes.

    A formatting diff or a missing type annotation needs no CI run to find, and
    both tools are already installed in the virtualenv.
    """

    def test_a_staged_python_file_is_linted_and_type_checked(self, branched: Path) -> None:
        stub(branched / ".venv" / "bin", "ruff")
        stub(branched / ".venv" / "bin", "mypy")
        (branched / "m.py").write_text("x = 1\n")
        git("add", "m.py", cwd=branched)

        result = git("commit", "-m", "python", cwd=branched)

        assert result.returncode == 0, result.stderr
        assert calls(branched) == [
            "ruff check .",
            "ruff format --check .",
            "mypy ",
        ]

    def test_the_commit_is_refused_when_lint_fails(self, branched: Path) -> None:
        stub(branched / ".venv" / "bin", "ruff", exit_code=1)
        stub(branched / ".venv" / "bin", "mypy")
        (branched / "m.py").write_text("x = 1\n")
        git("add", "m.py", cwd=branched)

        result = git("commit", "-m", "python", cwd=branched)

        assert result.returncode != 0
        assert "ruff check" in result.stderr

    def test_the_commit_is_refused_when_types_fail(self, branched: Path) -> None:
        stub(branched / ".venv" / "bin", "ruff")
        stub(branched / ".venv" / "bin", "mypy", exit_code=1)
        (branched / "m.py").write_text("x = 1\n")
        git("add", "m.py", cwd=branched)

        result = git("commit", "-m", "python", cwd=branched)

        assert result.returncode != 0
        assert "mypy" in result.stderr

    def test_a_refusal_says_it_can_be_bypassed(self, branched: Path) -> None:
        """Same bargain the branch rule strikes: deliberate, not impossible."""
        stub(branched / ".venv" / "bin", "ruff", exit_code=1)
        stub(branched / ".venv" / "bin", "mypy")
        (branched / "m.py").write_text("x = 1\n")
        git("add", "m.py", cwd=branched)

        result = git("commit", "-m", "python", cwd=branched)

        assert "--no-verify" in result.stderr


class TestItChecksNothingWithoutPython:
    """A prose commit must not be blocked by a Python error somewhere else.

    The gate checks the whole tree, and so does this hook -- running the gate's
    own commands is what keeps the two from drifting. The guard that makes that
    tolerable is staging: an edit to a Markdown file runs nothing at all, so a
    half-finished module in the working tree cannot hold documentation hostage.
    """

    def test_a_markdown_only_commit_runs_no_checks(self, branched: Path) -> None:
        stub(branched / ".venv" / "bin", "ruff", exit_code=1)
        stub(branched / ".venv" / "bin", "mypy", exit_code=1)
        (branched / "README.md").write_text("prose\n")
        git("add", "README.md", cwd=branched)

        result = git("commit", "-m", "prose", cwd=branched)

        assert result.returncode == 0, result.stderr
        assert calls(branched) == []


class TestItDoesNotRequireAVirtualenv:
    """A fresh clone has no `.venv`, and must still be able to commit.

    A hook that refuses when its tools are absent stops being a fast check and
    becomes a setup step, which is how hooks get deleted.
    """

    def test_a_missing_toolchain_does_not_block(self, branched: Path) -> None:
        (branched / "m.py").write_text("x = 1\n")
        git("add", "m.py", cwd=branched)

        result = git("commit", "-m", "python", cwd=branched)

        assert result.returncode == 0, result.stderr

    def test_it_says_what_it_skipped(self, branched: Path) -> None:
        (branched / "m.py").write_text("x = 1\n")
        git("add", "m.py", cwd=branched)

        result = git("commit", "-m", "python", cwd=branched)

        assert "ruff" in result.stderr
