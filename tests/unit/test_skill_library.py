"""Keeping a vault's canonical Skills current with the ones Never4gA ships.

`core/01` seeds `50_System/Skills/` at `init` and `core/04` section 20 makes the
vault canonical for a Skill, so a user edit is honoured exactly as an edited
template is. `VaultInitializer` writes only "unless something is already
there", so without a refresh every vault would keep the text it was born with,
and `adapters doctor`, which compares clients against the vault, would call
that healthy.

`core/04` section 25's ownership rules are written for the vault-to-client
hop. The same three apply one layer up and are what these tests pin: never
overwrite an edit (rule 1 and 4), report drift rather than resolve it (rule 5),
and support a dry run (rule 7). What makes it decidable without machine-local
state is that each
seeded Skill records the hash of the body it was seeded with, so an edit is a
property of the file rather than a memory of having written it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.services import VaultInitializer
from never4ga.services.adapters import SKILLS_DIRECTORY
from never4ga.services.doctor import Doctor, Finding
from never4ga.services.skill_library import SkillLibrary, SkillState


def fixed_clock() -> datetime:
    return datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    VaultInitializer(
        FileSystemVaultFileStore(root), FileSystemMarkdownStore(root), now=fixed_clock
    ).initialize("Test Vault")
    return root


@pytest.fixture
def library(vault: Path) -> SkillLibrary:
    return SkillLibrary(FileSystemVaultFileStore(vault))


def skill_path(name: str) -> VaultPath:
    return VaultPath.parse(f"{SKILLS_DIRECTORY}/{name}/SKILL.md")


def rewrite(vault: Path, name: str, body: str) -> None:
    """Edit a seeded Skill the way a user would -- body only, frontmatter kept."""
    path = vault / SKILLS_DIRECTORY / name / "SKILL.md"
    head, _, _ = path.read_text().partition("\n---\n")
    path.write_text(f"{head}\n---\n{body}")


class TestAFreshVaultIsCurrent:
    def test_every_seeded_skill_reports_current(self, library: SkillLibrary) -> None:
        states = {status.name: status.state for status in library.status()}
        assert states, "init seeds the canonical Skills"
        assert set(states.values()) == {SkillState.CURRENT}

    def test_a_current_vault_has_nothing_to_refresh(self, library: SkillLibrary) -> None:
        assert library.refresh(apply=False) == ()


class TestAnOutdatedSkillIsVisibleAndFixable:
    """The product moved on and the vault did not."""

    def test_an_older_version_reports_outdated(self, vault: Path, library: SkillLibrary) -> None:
        _age(vault, "never4ga-wrap")
        states = {status.name: status.state for status in library.status()}
        assert states["never4ga-wrap"] == SkillState.OUTDATED

    def test_refresh_without_apply_changes_nothing(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        _age(vault, "never4ga-wrap")
        before = (vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md").read_text()

        planned = library.refresh(apply=False)

        assert [change.name for change in planned] == ["never4ga-wrap"]
        assert all(not change.applied for change in planned)
        after = (vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md").read_text()
        assert after == before, "a dry run is a dry run (section 25 rule 7)"

    def test_refresh_with_apply_installs_the_shipped_text(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        _age(vault, "never4ga-wrap")

        applied = library.refresh(apply=True)

        assert [change.name for change in applied] == ["never4ga-wrap"]
        assert all(change.applied for change in applied)
        assert {s.state for s in library.status()} == {SkillState.CURRENT}


class TestAnEditIsNeverDestroyed:
    """core/04 section 25 rules 1 and 4, applied to the seed boundary."""

    def test_an_edited_skill_reports_edited(self, vault: Path, library: SkillLibrary) -> None:
        rewrite(vault, "never4ga-wrap", "# Mine\n\nI rewrote this.\n")
        states = {status.name: status.state for status in library.status()}
        assert states["never4ga-wrap"] == SkillState.EDITED

    def test_an_edited_skill_is_not_refreshed_even_when_outdated(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        _age(vault, "never4ga-wrap")
        rewrite(vault, "never4ga-wrap", "# Mine\n\nI rewrote this.\n")
        mine = (vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md").read_text()

        library.refresh(apply=True)

        after = (vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md").read_text()
        assert after == mine, "an edit outranks a newer shipped version"

    def test_force_overwrites_an_edit_because_the_user_asked(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        rewrite(vault, "never4ga-wrap", "# Mine\n\nI rewrote this.\n")

        library.refresh(apply=True, force=True)

        assert {s.state for s in library.status()} == {SkillState.CURRENT}


class TestAMissingSkillIsRestored:
    def test_a_deleted_skill_reports_missing_and_is_restored(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        (vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md").unlink()

        states = {status.name: status.state for status in library.status()}
        assert states["never4ga-wrap"] == SkillState.MISSING

        library.refresh(apply=True)
        assert (vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md").exists()


class TestAVaultSeededBeforeProvenanceExisted:
    """A Skill seeded before seed hashes were recorded.

    A Skill with no recorded seed hash cannot be proven unedited, so it is never
    overwritten silently -- it is reported, and `--force` is the way through.
    """

    def test_a_skill_without_a_seed_hash_reports_unknown_provenance(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        path = vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md"
        text = path.read_text()
        path.write_text(
            "\n".join(line for line in text.splitlines() if "never4ga-seed-hash" not in line) + "\n"
        )
        states = {status.name: status.state for status in library.status()}
        assert states["never4ga-wrap"] == SkillState.UNKNOWN_PROVENANCE

    def test_unknown_provenance_is_not_overwritten_without_force(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        path = vault / SKILLS_DIRECTORY / "never4ga-wrap" / "SKILL.md"
        stripped = (
            "\n".join(
                line for line in path.read_text().splitlines() if "never4ga-seed-hash" not in line
            )
            + "\n"
        )
        path.write_text(stripped)

        library.refresh(apply=True)

        assert path.read_text() == stripped


def _age(vault: Path, name: str) -> None:
    """Make a seeded Skill look like it came from an older Never4gA.

    Only the declared version moves: the body still hashes to its recorded seed,
    which is exactly the "untouched but outdated" case.
    """
    path = vault / SKILLS_DIRECTORY / name / "SKILL.md"
    text = path.read_text()
    aged = [
        '  never4ga-version: "0.0.1"' if "never4ga-version" in line else line
        for line in text.splitlines()
    ]
    path.write_text("\n".join(aged) + "\n")


class TestDoctorSaysSo:
    """`doctor` reports a Skill older than the product.

    `adapters doctor` compares clients against the vault and so is right to
    call a uniformly stale set "in sync". `doctor` looks at the vault itself,
    which is the level where a Skill older than the product is visible.
    """

    def test_an_outdated_skill_is_a_warning(self, vault: Path) -> None:
        _age(vault, "never4ga-wrap")
        findings = _diagnose(vault)
        codes = {finding.code for finding in findings}
        assert "skill_is_outdated" in codes

    def test_a_current_vault_reports_nothing_about_skills(self, vault: Path) -> None:
        codes = {finding.code for finding in _diagnose(vault)}
        assert "skill_is_outdated" not in codes

    def test_an_edited_skill_is_not_reported_as_outdated(self, vault: Path) -> None:
        rewrite(vault, "never4ga-wrap", "# Mine\n\nI rewrote this.\n")
        codes = {finding.code for finding in _diagnose(vault)}
        assert "skill_is_outdated" not in codes, "an edit is the user's, not drift"


def _diagnose(vault: Path) -> tuple[Finding, ...]:

    return (
        Doctor(
            FileSystemVaultFileStore(vault),
            FileSystemMarkdownStore(vault),
        )
        .diagnose()
        .findings
    )


class TestTheProductMovingOnWithoutAVersionBump:
    """A shipped Skill whose body changed without a version bump is outdated.

    The seed hash answers "did somebody edit this?" by rehashing the vault's
    own body. The vault copy is also compared against the *shipped* body, not
    only its version string, so a content change with no version bump is not
    reported CURRENT forever. An author cannot be relied on to bump a version.
    """

    def test_a_changed_body_at_the_same_version_reports_outdated(
        self, vault: Path, library: SkillLibrary, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from never4ga.services import scaffold

        name = "never4ga-startup"
        shipped = dict(scaffold.SKILLS)
        shipped[name] = shipped[name].replace(
            "Run this before substantive work, once.",
            "Run this before substantive work, once. And mind the flag order.",
        )
        monkeypatch.setattr(scaffold, "SKILLS", shipped)
        monkeypatch.setattr("never4ga.services.skill_library.SKILLS", shipped)

        states = {status.name: status.state for status in library.status()}
        assert states[name] is SkillState.OUTDATED

    def test_refresh_carries_the_change_into_the_vault(
        self, vault: Path, library: SkillLibrary, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from never4ga.services import scaffold

        name = "never4ga-startup"
        marker = "And mind the flag order."
        shipped = dict(scaffold.SKILLS)
        shipped[name] = shipped[name].replace(
            "Run this before substantive work, once.",
            f"Run this before substantive work, once. {marker}",
        )
        monkeypatch.setattr(scaffold, "SKILLS", shipped)
        monkeypatch.setattr("never4ga.services.skill_library.SKILLS", shipped)

        assert [change.name for change in library.refresh(apply=True)] == [name]
        assert marker in (vault / SKILLS_DIRECTORY / name / "SKILL.md").read_text()

    def test_an_unchanged_shipped_skill_is_still_current(self, library: SkillLibrary) -> None:
        """An unchanged shipped Skill is not reported OUTDATED.

        Reporting everything OUTDATED would make `refresh` rewrite the whole
        library on every run, which is how a local edit gets destroyed.
        """
        states = {status.name: status.state for status in library.status()}
        assert set(states.values()) == {SkillState.CURRENT}

    def test_a_local_edit_still_wins_over_a_shipped_change(
        self, vault: Path, library: SkillLibrary, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """core/04 section 25 rule 4 outranks staleness: never destroy an edit silently.

        When both sides moved, the answer is EDITED -- the state that needs a
        person -- not OUTDATED, which `refresh` would quietly overwrite.
        """
        from never4ga.services import scaffold

        name = "never4ga-startup"
        rewrite(vault, name, "# Mine now\n")
        shipped = dict(scaffold.SKILLS)
        shipped[name] = shipped[name].replace("Run this", "Run that")
        monkeypatch.setattr(scaffold, "SKILLS", shipped)
        monkeypatch.setattr("never4ga.services.skill_library.SKILLS", shipped)

        states = {status.name: status.state for status in library.status()}
        assert states[name] is SkillState.EDITED
        assert [change.name for change in library.refresh(apply=False)] == []


class TestTheMessageSaysWhichThingChanged:
    """ "Both at 0.4.0" is only true when both are.

    That wording is for a body that moved with no version bump, where "at
    0.3.0; this build ships 0.3.0" would argue against itself. A deliberate
    edit usually changes the body *and* the version, and then the message must
    name both versions rather than claim "both at 0.4.0" for a vault copy that
    holds 0.3.0.
    """

    def _message(self, vault: Path) -> str:
        from never4ga.services.doctor import Doctor

        findings = Doctor(
            FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault)
        ).diagnose()
        return next(f.message for f in findings.findings if f.code == "skill_is_outdated")

    def _ship(self, monkeypatch: pytest.MonkeyPatch, name: str, *, version: str | None) -> None:
        from never4ga.services import scaffold

        shipped = dict(scaffold.SKILLS)
        body = shipped[name].replace(
            "Run this before substantive work, once.",
            "Run this before substantive work, once. And something else.",
        )
        current = re.search(r'never4ga-version: "([^"]+)"', body).group(1)  # type: ignore[union-attr]
        if version is not None:
            body = body.replace(f'never4ga-version: "{current}"', f'never4ga-version: "{version}"')
        self.shipped_version = current
        shipped[name] = body
        monkeypatch.setattr(scaffold, "SKILLS", shipped)
        monkeypatch.setattr("never4ga.services.skill_library.SKILLS", shipped)

    def test_a_bumped_version_is_reported_as_a_change_not_as_both(
        self, vault: Path, library: SkillLibrary, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._ship(monkeypatch, "never4ga-startup", version="0.4.0")

        message = self._message(vault)
        assert "both at" not in message
        assert self.shipped_version in message and "0.4.0" in message

    def test_an_unchanged_version_still_says_the_version_is_not_the_point(
        self, vault: Path, library: SkillLibrary, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control: a body change with no version bump keeps its wording."""
        self._ship(monkeypatch, "never4ga-startup", version=None)

        message = self._message(vault)
        assert f"both at {self.shipped_version}" in message
        assert "the version is not what changed" in message
