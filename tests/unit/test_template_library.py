"""Templates get the same upgrade path as seeded Skills, by another mechanism.

`init` seeds `50_System/Templates/` through the same writer that seeds Skills,
with the same contract -- "write, unless something is already there" -- so a
template improved in the product would never reach an existing vault. Each
seeded file records the hash it was seeded with, so a refresh can tell an
outdated copy from an edited one and replace only the first.

**The provenance cannot live in the file.** A Skill has a `metadata` block
Never4gA owns, so its seed hash sits there and travels with it. A template is a
*concept skeleton*: its frontmatter is a set of placeholders that a person
copies in Obsidian to start a new note, so a field added there would be copied
into every concept made from it. The hash goes in a manifest beside the library
instead -- in the vault, so provenance travels with the vault rather than
sitting on one machine.

`50_System/Templates/` is a declared foreign-format island (`core/02` section
3.3): nothing there is validated or indexed as a concept, which is what makes it
a legitimate home for a JSON manifest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.services import VaultInitializer, scaffold
from never4ga.services.doctor import Doctor
from never4ga.services.scaffold import skill_body_hash
from never4ga.services.skill_library import SEED_MANIFEST, SkillLibrary, SkillState

TEMPLATES = "50_System/Templates"
A_TEMPLATE = "knowledge.md"


def fixed_clock() -> datetime:
    return datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


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


def template(vault: Path) -> Path:
    return vault / TEMPLATES / A_TEMPLATE


def states(library: SkillLibrary) -> dict[str, SkillState]:
    return {status.name: status.state for status in library.template_status()}


def age(vault: Path, name: str) -> None:
    """Make a seeded template look like it came from an older Never4gA.

    Both move together, which is what "outdated" means: an older build wrote
    different text *and* recorded that text's hash, so the file still matches
    its own record and only the product has moved on. Changing the record alone
    would simulate an edit, which is the other case entirely.
    """
    older = "---\ntype: knowledge\n---\n\n# An older shape\n"
    (vault / TEMPLATES / name).write_text(older)
    path = vault / TEMPLATES / SEED_MANIFEST
    recorded = json.loads(path.read_text())
    recorded[name] = skill_body_hash(older)
    path.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")


class TestAFreshVaultIsCurrent:
    def test_every_seeded_template_reports_current(self, library: SkillLibrary) -> None:
        found = states(library)
        assert found
        assert set(found.values()) == {SkillState.CURRENT}

    def test_init_writes_the_manifest(self, vault: Path) -> None:
        assert (vault / TEMPLATES / SEED_MANIFEST).exists()

    def test_the_manifest_is_not_a_concept(self, vault: Path) -> None:
        """It lives in a foreign-format island and must never be indexed."""
        store = FileSystemMarkdownStore(vault)
        assert SEED_MANIFEST not in {d.path.segments[-1] for d in store.iter_documents()}
        assert not any(problem.path.segments[-1] == SEED_MANIFEST for problem in store.problems())


class TestAnOutdatedTemplateIsVisibleAndFixable:
    def test_it_reports_outdated(self, vault: Path, library: SkillLibrary) -> None:
        age(vault, A_TEMPLATE)

        assert states(library)[A_TEMPLATE] == SkillState.OUTDATED

    def test_a_dry_run_changes_nothing(self, vault: Path, library: SkillLibrary) -> None:
        age(vault, A_TEMPLATE)
        before = template(vault).read_text()

        planned = library.refresh_templates(apply=False)

        assert [change.name for change in planned] == [A_TEMPLATE]
        assert template(vault).read_text() == before

    def test_apply_restores_the_shipped_text_and_the_hash(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        age(vault, A_TEMPLATE)

        library.refresh_templates(apply=True)

        assert states(library)[A_TEMPLATE] == SkillState.CURRENT


class TestAnEditIsNeverDestroyed:
    def test_an_edited_template_reports_edited(self, vault: Path, library: SkillLibrary) -> None:
        template(vault).write_text("---\nmine: true\n---\n\n# Mine\n")

        assert states(library)[A_TEMPLATE] == SkillState.EDITED

    def test_an_edited_template_is_left_alone(self, vault: Path, library: SkillLibrary) -> None:
        age(vault, A_TEMPLATE)
        mine = "---\nmine: true\n---\n\n# Mine\n"
        template(vault).write_text(mine)

        library.refresh_templates(apply=True)

        assert template(vault).read_text() == mine

    def test_force_replaces_it_because_the_user_asked(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        template(vault).write_text("---\nmine: true\n---\n\n# Mine\n")

        library.refresh_templates(apply=True, force=True)

        assert states(library)[A_TEMPLATE] == SkillState.CURRENT


class TestAVaultSeededBeforeTheManifestExisted:
    def test_no_manifest_means_unknown_provenance(self, vault: Path, library: SkillLibrary) -> None:
        (vault / TEMPLATES / SEED_MANIFEST).unlink()

        assert set(states(library).values()) == {SkillState.UNKNOWN_PROVENANCE}

    def test_it_is_not_replaced_without_force(self, vault: Path, library: SkillLibrary) -> None:
        (vault / TEMPLATES / SEED_MANIFEST).unlink()
        mine = "---\nmine: true\n---\n\n# Mine\n"
        template(vault).write_text(mine)

        library.refresh_templates(apply=True)

        assert template(vault).read_text() == mine


class TestDoctorSaysSo:
    def test_an_outdated_template_is_a_warning(self, vault: Path) -> None:
        age(vault, A_TEMPLATE)
        codes = {
            finding.code
            for finding in Doctor(FileSystemVaultFileStore(vault), FileSystemMarkdownStore(vault))
            .diagnose()
            .findings
        }
        assert "template_is_outdated" in codes


class TestTheSkillTemplateTravelsTheSameWay:
    """`skill.md` is seeded beside the concept skeletons and reaches an older
    vault through the same refresh, on the same terms."""

    def test_it_is_seeded_and_current(self, vault: Path, library: SkillLibrary) -> None:
        assert (vault / TEMPLATES / "skill.md").read_text() == scaffold.SKILL_TEMPLATE
        assert states(library)["skill.md"] is SkillState.CURRENT

    def test_a_vault_from_before_it_existed_gets_it_on_refresh(
        self, vault: Path, library: SkillLibrary
    ) -> None:
        (vault / TEMPLATES / "skill.md").unlink()
        path = vault / TEMPLATES / SEED_MANIFEST
        recorded = json.loads(path.read_text())
        del recorded["skill.md"]
        path.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")
        assert states(library)["skill.md"] is SkillState.MISSING
        library.refresh_templates(apply=True)
        assert (vault / TEMPLATES / "skill.md").read_text() == scaffold.SKILL_TEMPLATE
        assert states(library)["skill.md"] is SkillState.CURRENT

    def test_an_edited_copy_is_never_overwritten(self, vault: Path, library: SkillLibrary) -> None:
        # A user who reshaped the template for their own Skills keeps it.
        (vault / TEMPLATES / "skill.md").write_text("---\nname: <name>\n---\n# Mine\n")
        library.refresh_templates(apply=True)
        assert (vault / TEMPLATES / "skill.md").read_text() == "---\nname: <name>\n---\n# Mine\n"
        assert states(library)["skill.md"] is SkillState.EDITED
