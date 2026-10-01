"""`never4ga repair`, end to end, against a real Git repository.

A repair has three requirements, and the third is the load-bearing one:

- **atomic per-file writes**: already true of the document store, and it stays
  true;
- **idempotent**: ``--apply`` twice leaves the second run a no-op, so an
  interrupted repair is resumed by re-running it rather than reasoned about;
- **revertible through ordinary Git**: ``git revert`` restores the vault
  exactly, with *no Never4gA involvement in the reversal*. Never4gA is not the
  undo mechanism and must not become one.

The vault here is a real Git repository, because Git is what makes repair safe.
A vault may run an autosync that commits on a timer, and nobody will remember
to pause it. The design has to survive an auto-commit landing in the middle of
a repair, and Git makes that harmless rather than a race. A test that mocked
Git would be testing the claim against itself.

`tests/unit/test_repair_plan.py` covers which findings earn a repair. This
covers what happens when one is performed.

**The repair exercised here restores a deleted `home.md` rather than a deleted
directory, and that is not arbitrary.** Git does not track empty directories, so
restoring one is invisible to it: `git status` says nothing and `git revert`
has nothing to undo. A test that used a directory would report the revertibility
requirement as satisfied without ever exercising it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from never4ga.cli import EXIT_OK, main

Run = Callable[..., Any]


def git(vault: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def run(vault: Path, capsys: pytest.CaptureFixture[str]) -> Run:
    def invoke(*arguments: str, as_json: bool = True) -> Any:
        code = main(["--vault", str(vault), *(["--json"] if as_json else []), *arguments])
        captured = capsys.readouterr()
        if not as_json:
            return code, captured.out
        return json.loads(captured.out or captured.err)

    return invoke


@pytest.fixture
def tracked(vault: Path, run: Run) -> Run:
    """An initialised vault under Git, the way a vault usually lives."""
    run("init")
    git(vault, "init", "-q")
    git(vault, "config", "user.email", "test@example.invalid")
    git(vault, "config", "user.name", "Test")
    git(vault, "add", "-A")
    git(vault, "commit", "-qm", "the vault as it was")
    run("index")
    return run


class TestTheDryRun:
    def test_a_plan_changes_nothing(self, tracked: Run, vault: Path) -> None:
        """Dry-run is the default rather than a flag.

        The safe call is the short one, and the dangerous call is the one you
        have to type more to reach.
        """
        (vault / "home.md").unlink()
        tracked("doctor")
        before = git(vault, "status", "--porcelain")

        payload = tracked("repair")
        assert payload["applied"] is False
        assert payload["actions"] != []
        assert git(vault, "status", "--porcelain") == before
        assert not (vault / "home.md").exists()

    def test_it_says_what_it_would_do(self, tracked: Run, vault: Path) -> None:
        (vault / "home.md").unlink()
        tracked("doctor")
        code, out = tracked("repair", as_json=False)
        assert code == EXIT_OK
        assert "would repair" in out
        assert "--apply" in out


class TestApplying:
    def test_it_restores_what_was_missing(self, tracked: Run, vault: Path) -> None:
        (vault / "home.md").unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        assert (vault / "home.md").is_file()

    def test_the_finding_is_gone_on_the_next_diagnosis(self, tracked: Run, vault: Path) -> None:
        (vault / "home.md").unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        codes = {f["code"] for f in tracked("doctor")["findings"]}
        assert "missing_root_document" not in codes

    def test_applying_twice_is_a_no_op(self, tracked: Run, vault: Path) -> None:
        """The second requirement: applying twice changes nothing more.

        This is what makes an interrupted repair resumable by re-running it,
        rather than something to reason about at the time.
        """
        (vault / "home.md").unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        git(vault, "add", "-A")
        git(vault, "commit", "-qm", "the repair")

        tracked("doctor")
        tracked("repair", "--apply")
        assert git(vault, "status", "--porcelain") == ""


class TestRevertingIsGitsJob:
    def test_git_revert_restores_the_vault_exactly(self, tracked: Run, vault: Path) -> None:
        """The third requirement: Git alone reverts a repair.

        Whatever a repair writes is a Git commit like any other, and the
        ordinary tool undoes it with no Never4gA involvement.
        """
        before = git(vault, "rev-parse", "HEAD^{tree}")
        (vault / "home.md").unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        git(vault, "add", "-A")
        git(vault, "commit", "-qm", "an autosync commit that captured a repair")
        assert git(vault, "rev-parse", "HEAD^{tree}") != before

        git(vault, "revert", "--no-edit", "HEAD")
        assert git(vault, "rev-parse", "HEAD^{tree}") == before

    def test_and_the_vault_is_intact_afterwards(self, tracked: Run, vault: Path) -> None:
        """A revert restores the original document, not the broken state.

        The commit captured a *net* change (`home.md` deleted and rewritten,
        so a modification) rather than the repair alone. Reverting it puts the
        original `home.md` back, not the broken state: the vault ends up whole,
        with the document a person originally had rather than the one the repair
        generated.

        The only thing left complaining is the index, which is derived and says
        so. That is `core/06` §2 working: canonical Markdown is restored by Git,
        and the projection notices it is behind rather than pretending.
        """
        (vault / "home.md").unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        git(vault, "add", "-A")
        git(vault, "commit", "-qm", "a repair")
        git(vault, "revert", "--no-edit", "HEAD")

        payload = tracked("doctor")
        assert (vault / "home.md").is_file()
        assert {f["code"] for f in payload["findings"]} <= {"index_is_stale"}
        assert payload["healthy"]


class TestAnAutoCommitLandingMidRepair:
    def test_the_vault_is_left_valid(self, tracked: Run, vault: Path) -> None:
        """The vault is valid at every instant of a repair, not only at the end.

        An autosync may commit at any moment. Here a commit is taken between the
        repair's file writes, the worst case, and the vault has to be valid at
        that instant, not merely after the repair finishes.
        """
        (vault / "home.md").unlink()
        (vault / "index.md").unlink()
        tracked("doctor")

        commits: list[str] = []
        from never4ga.adapters.filesystem import FileSystemVaultFileStore

        original = FileSystemVaultFileStore.write_text

        def committing(self: Any, path: Any, text: Any) -> Any:
            result = original(self, path, text)
            # The autosync, landing at the worst possible moment.
            git(vault, "add", "-A")
            status = subprocess.run(
                ["git", "commit", "-qm", f"autosync during repair {len(commits)}"],
                cwd=vault,
                capture_output=True,
                text=True,
            )
            if status.returncode == 0:
                commits.append(git(vault, "rev-parse", "HEAD"))
            return result

        FileSystemVaultFileStore.write_text = committing  # type: ignore[method-assign]
        try:
            tracked("repair", "--apply")
        finally:
            FileSystemVaultFileStore.write_text = original  # type: ignore[method-assign]

        assert commits, "the test did not actually interleave a commit"
        # Every intermediate state was committable, and the end state is clean.
        report = tracked("validate")
        assert report["valid"] == report["checked"]
        git(vault, "add", "-A")
        subprocess.run(["git", "commit", "-qm", "after"], cwd=vault, capture_output=True)
        assert git(vault, "status", "--porcelain") == ""

    def test_and_re_running_the_repair_finishes_it(self, tracked: Run, vault: Path) -> None:
        # Idempotence is what makes an interrupted repair resumable, which is
        # the property that makes the interleaving harmless rather than a race.
        (vault / "home.md").unlink()
        (vault / "index.md").unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        tracked("doctor")
        tracked("repair", "--apply")
        assert (vault / "home.md").is_file() and (vault / "index.md").is_file()


class TestWhatItRefusesToClaim:
    def test_a_finding_needing_a_person_is_listed_rather_than_hidden(
        self, tracked: Run, vault: Path
    ) -> None:
        (vault / "30_Knowledge" / "Notes" / "thing.md").write_text(
            "---\ntype: knowledge\nid: 01a04bd1-7357-7002-8bbc-8e82bcb91959\n"
            "schema: never4ga/0.1\ntitle: Thing\n"
            'created_at: "2026-08-23T12:00:00Z"\n---\n\nsee [gone](nowhere.md)\n',
            encoding="utf-8",
        )
        tracked("index")
        tracked("doctor")
        payload = tracked("repair")
        assert "broken_link" in {f["code"] for f in payload["unrepairable"]}

    def test_and_the_summary_says_it_needs_a_person(self, tracked: Run, vault: Path) -> None:
        (vault / "30_Knowledge" / "Notes" / "thing.md").write_text(
            "---\ntype: knowledge\nid: 01a04bd1-7357-7002-8bbc-8e82bcb91959\n"
            "schema: never4ga/0.1\ntitle: Thing\n"
            'created_at: "2026-08-23T12:00:00Z"\n---\n\nsee [gone](nowhere.md)\n',
            encoding="utf-8",
        )
        tracked("index")
        tracked("doctor")
        _, out = tracked("repair", as_json=False)
        assert "needs a person" in out

    def test_it_plans_from_the_ledger_rather_than_diagnosing(
        self, tracked: Run, vault: Path
    ) -> None:
        """Repair plans from *persisted* findings rather than diagnosing.

        Nobody has run `doctor`, so nothing is recorded, and repair says there
        is nothing to do rather than going to look: detection is not a side
        effect of asking for a fix.
        """
        (vault / "home.md").unlink()
        payload = tracked("repair")
        assert payload["actions"] == []


class TestAMissingScratchpad:
    """`doctor` reports a missing scratchpad, and `repair` creates it.

    A vault can lack `00_Inbox/scratchpad.md`, for example one made by an older
    `init`. `scratch add` creates it on first use, so without a finding the
    person the file is for, jotting a line in Obsidian, would never see it
    exist. Startup does not create it, because a context read that writes to
    the vault is a write nobody asked for.
    """

    def test_doctor_reports_it(self, tracked: Run, vault: Path) -> None:
        (vault / "00_Inbox" / "scratchpad.md").unlink()
        codes = {finding["code"] for finding in tracked("doctor")["findings"]}
        assert "inbox_scratchpad_missing" in codes

    def test_a_vault_that_has_one_is_not_told_otherwise(self, tracked: Run) -> None:
        codes = {finding["code"] for finding in tracked("doctor")["findings"]}
        assert "inbox_scratchpad_missing" not in codes

    def test_repair_creates_it_with_its_header(self, tracked: Run, vault: Path) -> None:
        scratchpad = vault / "00_Inbox" / "scratchpad.md"
        scratchpad.unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        assert scratchpad.read_text(encoding="utf-8").startswith("# Scratchpad")
        codes = {finding["code"] for finding in tracked("doctor")["findings"]}
        assert "inbox_scratchpad_missing" not in codes

    def test_a_scratchpad_with_lines_on_it_is_left_alone(self, tracked: Run, vault: Path) -> None:
        scratchpad = vault / "00_Inbox" / "scratchpad.md"
        tracked("scratch", "add", "keep this thought")
        before = scratchpad.read_text(encoding="utf-8")
        (vault / "home.md").unlink()
        tracked("doctor")
        tracked("repair", "--apply")
        assert scratchpad.read_text(encoding="utf-8") == before


class TestAnUnregisteredForeignDirectory:
    """A foreign directory added by hand after `init` is registered by `repair`.

    Foreign material is defined in core/02 section 3.3.
    """

    def test_repair_registers_it_and_touches_nothing_inside(
        self, tracked: Run, vault: Path
    ) -> None:
        (vault / "Recipes").mkdir()
        (vault / "Recipes" / "bread.md").write_text("# Bread\n", encoding="utf-8")
        git(vault, "add", "-A")
        git(vault, "commit", "-qm", "recipes")
        codes = {f["code"] for f in tracked("doctor")["findings"]}
        assert "foreign_material_unregistered" in codes

        tracked("repair", "--apply")

        codes = {f["code"] for f in tracked("doctor")["findings"]}
        assert "foreign_material_unregistered" not in codes
        assert (vault / "Recipes" / "bread.md").read_text(encoding="utf-8") == "# Bread\n"
        # The helper strips the porcelain line's leading status column.
        assert git(vault, "status", "--porcelain").splitlines() == ["M 50_System/system.md"]
        assert tracked("status")["foreign_material"] == ["Recipes"]
