"""A real vault on disk, from `init` through creation to `doctor`.

These tests build a vault in a temporary directory using the real filesystem
adapters, then check the properties core/01 promises of a usable canonical
vault.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import (
    FileSystemMarkdownStore,
    FileSystemVaultFileStore,
    parse_document,
)
from never4ga.domain.document import VaultPath
from never4ga.layout import (
    FOREIGN_MATERIAL_FIELD,
    SYSTEM_MANIFEST,
    TOOL_CONVENTION_FILENAMES,
    VaultRoot,
)
from never4ga.schema import Severity, ValidationLevel, validate_document
from never4ga.services import (
    ConceptCreationError,
    ContentService,
    Doctor,
    VaultInitializer,
)


def fixed_clock() -> datetime:
    return datetime(2026, 8, 22, 19, 0, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def files(root: Path) -> FileSystemVaultFileStore:
    return FileSystemVaultFileStore(root)


@pytest.fixture
def documents(root: Path) -> FileSystemMarkdownStore:
    return FileSystemMarkdownStore(root)


@pytest.fixture
def initialized(
    files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore
) -> FileSystemMarkdownStore:
    VaultInitializer(files, documents, now=fixed_clock).initialize("Test Vault")
    documents.refresh()
    return documents


@pytest.fixture
def content(
    files: FileSystemVaultFileStore, initialized: FileSystemMarkdownStore
) -> ContentService:
    return ContentService(files, initialized, now=fixed_clock)


class TestInitializationStructure:
    def test_the_seven_roots_exist(self, initialized: object, root: Path) -> None:
        for vault_root in VaultRoot:
            assert (root / str(vault_root)).is_dir(), vault_root

    def test_no_eighth_top_level_directory_is_invented(
        self, initialized: object, root: Path
    ) -> None:
        # core/01 section 1: the conceptual roots are intentionally stable.
        directories = {p.name for p in root.iterdir() if p.is_dir()}
        assert directories == {str(vault_root) for vault_root in VaultRoot}

    def test_only_index_and_home_sit_at_the_root(self, initialized: object, root: Path) -> None:
        assert {p.name for p in root.iterdir() if p.is_file()} == {"index.md", "home.md"}

    def test_the_knowledge_subdirectories_exist(self, initialized: object, root: Path) -> None:
        for name in ("Notes", "Maps", "Assets"):
            assert (root / "30_Knowledge" / name).is_dir()

    def test_there_is_no_entities_root(self, initialized: object, root: Path) -> None:
        # core/01 section 9: an entity record was a knowledge note in a second
        # place, so the root is retired. Six roots, not seven.
        assert not (root / "40_Entities").exists()

    def test_the_system_directories_exist(self, initialized: object, root: Path) -> None:
        for name in (
            "Standards",
            "Schemas",
            "Templates",
            "Skills",
            "Agents",
            "Documentation",
            "Integrations",
        ):
            assert (root / "50_System" / name).is_dir(), name

    def test_archive_subfolders_are_not_created_speculatively(
        self, initialized: object, root: Path
    ) -> None:
        # core/01 section 11: "Create subfolders only when used."
        assert [p.name for p in (root / "90_Archive").iterdir()] == ["index.md"]

    def test_every_root_has_navigation(self, initialized: object, root: Path) -> None:
        for vault_root in VaultRoot:
            assert (root / str(vault_root) / "index.md").is_file(), vault_root


class TestInitializationDocuments:
    def test_the_root_index_declares_the_okf_version(self, initialized: object, root: Path) -> None:
        parsed = parse_document((root / "index.md").read_text(encoding="utf-8"))
        assert parsed.frontmatter == {"okf_version": "0.2"}

    def test_the_root_index_carries_no_concept_frontmatter(
        self, initialized: object, root: Path
    ) -> None:
        # core/02 section 4: the only index.md permitted frontmatter, and only this.
        parsed = parse_document((root / "index.md").read_text(encoding="utf-8"))
        assert parsed.frontmatter is not None
        assert "id" not in parsed.frontmatter

    def test_directory_indexes_carry_no_frontmatter_at_all(
        self, initialized: object, root: Path
    ) -> None:
        for vault_root in VaultRoot:
            text = (root / str(vault_root) / "index.md").read_text(encoding="utf-8")
            assert parse_document(text).frontmatter is None, vault_root

    def test_home_is_a_dashboard_concept(self, initialized: FileSystemMarkdownStore) -> None:
        home = initialized.get_by_path(VaultPath.parse("home.md"))
        assert home is not None
        assert home.frontmatter["type"] == "dashboard"

    def test_the_system_manifest_is_the_vault_identity(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore
    ) -> None:
        result = VaultInitializer(files, documents, now=fixed_clock).initialize("Test Vault")
        documents.refresh()
        manifest = documents.get_by_path(VaultPath.parse("50_System/system.md"))
        assert manifest is not None
        assert manifest.concept_id == result.vault_id
        assert manifest.frontmatter["type"] == "system_manifest"

    def test_scaffolded_concepts_declare_who_generated_them(
        self, initialized: FileSystemMarkdownStore
    ) -> None:
        # core/02 section 5.2: process-created content MUST include generated.
        manifest = initialized.get_by_path(VaultPath.parse("50_System/system.md"))
        assert manifest is not None
        assert manifest.frontmatter["generated"] == {
            "by": "process:never4ga",
            "at": "2026-08-22T19:00:00Z",
        }

    def test_timestamps_are_written_quoted(self, initialized: object, root: Path) -> None:
        # core/02 section 7.4.
        text = (root / "50_System" / "system.md").read_text(encoding="utf-8")
        assert 'created_at: "2026-08-22T19:00:00Z"' in text

    def test_templates_are_installed(self, initialized: object, root: Path) -> None:
        templates = {p.name for p in (root / "50_System" / "Templates").iterdir()}
        assert "knowledge.md" in templates
        assert "workspace.md" in templates
        assert len(templates) >= 10

    def test_template_filenames_follow_adr_0008(self, initialized: object, root: Path) -> None:
        # Files are lowercase and hyphens join the words of one idea, so the
        # template is `life-area.md` while the type value stays `life_area`.
        names = {p.name for p in (root / "50_System" / "Templates").iterdir()}
        assert {"life-area.md", "research-note.md", "activity-log.md"} <= names
        assert not [name for name in names if "_" in name], sorted(names)

    def test_templates_are_not_treated_as_concepts(
        self, initialized: FileSystemMarkdownStore
    ) -> None:
        # core/02 section 3.3: foreign-format files are never rewritten as concepts.
        paths = {str(document.path) for document in initialized.iter_documents()}
        assert not any(path.startswith("50_System/Templates/") for path in paths)
        assert initialized.problems() == ()

    def test_the_obsidian_pack_is_installed(self, initialized: object, root: Path) -> None:
        obsidian = root / "50_System" / "Integrations" / "Obsidian"
        assert (obsidian / "README.md").is_file()
        bases = {p.name for p in (obsidian / "Bases").iterdir()}
        assert "active-workspaces.base" in bases
        assert "stale-concepts.base" in bases

    def test_the_obsidian_pack_is_not_indexed_as_canonical_knowledge(
        self, initialized: FileSystemMarkdownStore
    ) -> None:
        paths = {str(document.path) for document in initialized.iter_documents()}
        assert not any("Obsidian" in path for path in paths)


class TestGeneratedVaultIsValid:
    def test_every_scaffolded_concept_passes_strict_validation(
        self, initialized: FileSystemMarkdownStore
    ) -> None:
        known = {document.concept_id for document in initialized.iter_documents()}
        for document in initialized.iter_documents():
            report = validate_document(
                document.path,
                document.frontmatter,
                level=ValidationLevel.STRICT,
                known_ids=known,
            )
            assert report.ok, (document.path, report.issues)

    def test_a_fresh_vault_is_healthy(
        self, files: FileSystemVaultFileStore, initialized: FileSystemMarkdownStore
    ) -> None:
        diagnosis = Doctor(files, initialized).diagnose()
        assert diagnosis.healthy, diagnosis.errors
        assert diagnosis.warnings == ()

    def test_the_generated_vault_is_readable_without_obsidian(
        self, initialized: object, root: Path
    ) -> None:
        # Every Markdown file must be plain text a human can read with no
        # plugin, and must not depend on a .base view.
        markdown = sorted(root.rglob("*.md"))
        assert len(markdown) > 15
        for path in markdown:
            text = path.read_text(encoding="utf-8")
            assert text.strip(), path
            assert "```base" not in text, path
            assert "```dataview" not in text, path

    def test_every_scaffolded_markdown_file_has_a_heading(
        self, initialized: object, root: Path
    ) -> None:
        for path in sorted(root.rglob("*.md")):
            if path.is_relative_to(root / "50_System" / "Templates"):
                continue
            body = parse_document(path.read_text(encoding="utf-8")).body
            assert body.lstrip().startswith("#"), path


class TestInitializationIsIdempotent:
    def test_running_twice_creates_nothing_the_second_time(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore
    ) -> None:
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        documents.refresh()
        second = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert second.created == ()
        assert second.already_initialized

    def test_the_vault_identity_survives_re_initialization(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore
    ) -> None:
        first = VaultInitializer(files, documents, now=fixed_clock).initialize()
        documents.refresh()
        second = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert second.vault_id == first.vault_id

    def test_user_edits_are_preserved(
        self,
        files: FileSystemVaultFileStore,
        documents: FileSystemMarkdownStore,
        root: Path,
    ) -> None:
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        edited = root / "50_System" / "Templates" / "knowledge.md"
        edited.write_text("my own template\n", encoding="utf-8")
        (root / "00_Inbox" / "index.md").write_text("# My Inbox\n", encoding="utf-8")

        documents.refresh()
        VaultInitializer(files, documents, now=fixed_clock).initialize()

        assert edited.read_text(encoding="utf-8") == "my own template\n"
        assert (root / "00_Inbox" / "index.md").read_text(encoding="utf-8") == "# My Inbox\n"

    def test_initializing_a_directory_that_already_has_content_keeps_it(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        (root / "30_Knowledge" / "Notes").mkdir(parents=True)
        (root / "30_Knowledge" / "Notes" / "Mine.md").write_text("mine", encoding="utf-8")
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert (root / "30_Knowledge" / "Notes" / "Mine.md").read_text(encoding="utf-8") == "mine"


def a_pile(root: Path) -> None:
    """Somebody's notes, in the shape foreign material usually takes.

    Two nested folders, notes with and without frontmatter, a README, and an
    Obsidian settings directory. The frontmatter is the writer's own.
    """
    (root / "Projects" / "garden" / "meetings").mkdir(parents=True)
    (root / "Projects" / "garden" / "raised-beds.md").write_text(
        "---\ntags: [garden, soil]\nstatus: active\n---\n# Raised beds\n\nCompost ratios.\n",
        encoding="utf-8",
    )
    (root / "Projects" / "garden" / "meetings" / "2026-05-12.md").write_text(
        "# Tuesday sync\n\nDecided to buy the drip irrigation kit.\n", encoding="utf-8"
    )
    (root / "Reading").mkdir()
    (root / "Reading" / "thinking fast and slow.md").write_text(
        "---\ntitle: Thinking Fast and Slow\nauthor: Kahneman\n---\nNotes on system one.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# My notes\n", encoding="utf-8")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.is_file()
        and not path.relative_to(root).parts[0].startswith((".", "0", "1", "2", "3", "5", "9"))
        and path.name != "home.md"
        and path.name != "index.md"
    }


class TestAPileIsRegisteredAsForeignMaterial:
    """`init` over somebody's notes registers them as foreign material.

    Foreign material is defined in core/01 section 1 and core/02 section 3.3.
    `init` must notice the pile rather than succeed silently and report
    *preserved: 0*.
    """

    def test_every_top_level_directory_that_is_not_a_root_is_registered(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        result = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert [(f.directory, f.files) for f in result.foreign_material] == [
            ("Projects", 2),
            ("Reading", 1),
        ]

    def test_the_registration_is_recorded_in_the_system_manifest(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        manifest = documents.get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        assert manifest.frontmatter[FOREIGN_MATERIAL_FIELD] == ["Projects", "Reading"]

    def test_the_manifest_still_validates_strictly(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        manifest = documents.get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        report = validate_document(
            manifest.path, manifest.frontmatter, level=ValidationLevel.STRICT
        )
        assert [issue.message for issue in report.issues] == []

    def test_nothing_in_the_pile_is_touched(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        before = snapshot(root)
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert snapshot(root) == before
        assert (root / ".obsidian" / "app.json").read_text(encoding="utf-8") == "{}"

    def test_dot_directories_and_the_roots_are_not_foreign(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        (root / "30_Knowledge" / "Notes").mkdir(parents=True)
        result = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert {f.directory for f in result.foreign_material} == {"Projects", "Reading"}

    def test_an_empty_directory_is_still_the_users(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        (root / "Drafts").mkdir()
        result = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert [(f.directory, f.files) for f in result.foreign_material] == [("Drafts", 0)]

    def test_what_was_already_there_is_counted_as_preserved(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        # Preserved is everything `init` found and left alone, the pile
        # included.
        a_pile(root)
        (root / "README.md").write_text("# My notes\n", encoding="utf-8")
        result = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert sorted(str(path) for path in result.preserved) == [
            "Projects/garden/meetings/2026-05-12.md",
            "Projects/garden/raised-beds.md",
            "README.md",
            "Reading/thinking fast and slow.md",
        ]

    def test_a_second_run_still_counts_the_pile_beside_its_own_files(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        documents.refresh()
        again = VaultInitializer(files, documents, now=fixed_clock).initialize()
        preserved = {str(path) for path in again.preserved}
        assert "Projects/garden/raised-beds.md" in preserved
        assert str(SYSTEM_MANIFEST) in preserved
        assert again.created == ()
        assert len(preserved) == len(again.preserved)

    def test_a_dot_directory_is_not_counted(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        result = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert not any(str(path).startswith(".") for path in result.preserved)

    def test_a_fresh_vault_has_none(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore
    ) -> None:
        result = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert result.foreign_material == ()
        manifest = documents.get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        assert FOREIGN_MATERIAL_FIELD not in manifest.frontmatter

    def test_a_second_init_over_the_same_pile_changes_nothing(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        manifest_text = (root / "50_System" / "system.md").read_text(encoding="utf-8")
        documents.refresh()
        second = VaultInitializer(files, documents, now=fixed_clock).initialize()
        assert second.created == ()
        assert second.updated == ()
        assert [f.directory for f in second.foreign_material] == ["Projects", "Reading"]
        assert (root / "50_System" / "system.md").read_text(encoding="utf-8") == manifest_text

    def test_a_directory_added_later_is_registered_by_the_next_init(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        a_pile(root)
        first = VaultInitializer(files, documents, now=fixed_clock).initialize()
        (root / "Recipes").mkdir()
        (root / "Recipes" / "bread.md").write_text("# Bread\n", encoding="utf-8")
        documents.refresh()
        later = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
        second = VaultInitializer(files, documents, now=lambda: later).initialize()
        assert second.vault_id == first.vault_id
        assert second.updated == (SYSTEM_MANIFEST,)
        manifest = documents.get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None
        assert manifest.frontmatter[FOREIGN_MATERIAL_FIELD] == ["Projects", "Reading", "Recipes"]
        assert manifest.frontmatter["generated"]["at"] == "2026-09-14T12:00:00Z"

    def test_the_vault_is_otherwise_healthy_about_its_own_structure(
        self, files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore, root: Path
    ) -> None:
        # Registering the pile must not make doctor say anything about the
        # vault's own files.
        a_pile(root)
        VaultInitializer(files, documents, now=fixed_clock).initialize()
        documents.refresh()
        findings = Doctor(files, documents).diagnose().findings
        own = [
            f
            for f in findings
            if f.path is None or str(f.path).startswith(("50_System", "home", "index"))
        ]
        assert [(f.code, str(f.path)) for f in own] == []


class TestWorkspaceCreation:
    def test_a_workspace_gets_a_manifest_and_an_index(
        self, content: ContentService, root: Path
    ) -> None:
        created = content.create_workspace("BezaCore", workspace_type="organization")
        assert str(created.path) == "10_Workspaces/BezaCore/workspace.md"
        assert (root / "10_Workspaces" / "BezaCore" / "workspace.md").is_file()
        assert (root / "10_Workspaces" / "BezaCore" / "index.md").is_file()

    def test_no_tree_of_empty_directories_is_created(
        self, content: ContentService, root: Path
    ) -> None:
        # core/03 section 29: "MUST NOT create a giant tree of empty folders".
        content.create_workspace("BezaCore", workspace_type="organization")
        directory = root / "10_Workspaces" / "BezaCore"
        assert [p.name for p in directory.iterdir()] == ["index.md", "workspace.md"] or sorted(
            p.name for p in directory.iterdir()
        ) == ["index.md", "workspace.md"]

    def test_the_manifest_is_a_concept_and_the_index_is_not(
        self, content: ContentService, initialized: FileSystemMarkdownStore, root: Path
    ) -> None:
        content.create_workspace("BezaCore", workspace_type="organization")
        initialized.refresh()
        manifest = initialized.get_by_path(VaultPath.parse("10_Workspaces/BezaCore/workspace.md"))
        assert manifest is not None
        assert manifest.frontmatter["type"] == "workspace"

        index_text = (root / "10_Workspaces" / "BezaCore" / "index.md").read_text(encoding="utf-8")
        assert parse_document(index_text).frontmatter is None

    def test_a_workspace_manifest_validates_strictly(self, content: ContentService) -> None:
        created = content.create_workspace(
            "BezaCore", workspace_type="organization", description="Parent organization."
        )
        report = validate_document(
            created.path, created.document.frontmatter, level=ValidationLevel.STRICT
        )
        assert report.ok, report.issues

    def test_a_child_workspace_lives_under_the_parents_workspaces_directory(
        self, content: ContentService, root: Path
    ) -> None:
        parent = content.create_workspace("BezaCore", workspace_type="organization")
        child = content.create_workspace(
            "Sparrow", workspace_type="product", parent=parent.concept_id
        )
        assert str(child.path) == ("10_Workspaces/BezaCore/Workspaces/Sparrow/workspace.md")
        assert (root / "10_Workspaces/BezaCore/Workspaces/Sparrow/workspace.md").is_file()

    def test_a_child_records_its_parent_by_identity(self, content: ContentService) -> None:
        parent = content.create_workspace("BezaCore", workspace_type="organization")
        child = content.create_workspace(
            "Sparrow", workspace_type="product", parent=parent.concept_id
        )
        assert child.document.frontmatter["parent"] == str(parent.concept_id)

    def test_a_top_level_workspace_declares_no_parent(self, content: ContentService) -> None:
        created = content.create_workspace("BezaCore", workspace_type="organization")
        assert "parent" not in created.document.frontmatter

    def test_nesting_goes_more_than_one_level_deep(self, content: ContentService) -> None:
        a = content.create_workspace("A", workspace_type="organization")
        b = content.create_workspace("B", workspace_type="product", parent=a.concept_id)
        c = content.create_workspace("C", workspace_type="project", parent=b.concept_id)
        assert str(c.path) == ("10_Workspaces/A/Workspaces/B/Workspaces/C/workspace.md")

    def test_an_unknown_parent_is_refused(self, content: ContentService) -> None:
        from never4ga.domain.identity import ConceptId

        with pytest.raises(ConceptCreationError, match="does not exist"):
            content.create_workspace("Orphan", workspace_type="product", parent=ConceptId.new())

    def test_a_non_workspace_parent_is_refused(self, content: ContentService) -> None:
        note = content.create_knowledge("Hybrid Retrieval")
        with pytest.raises(ConceptCreationError, match="not a workspace"):
            content.create_workspace("Nope", workspace_type="product", parent=note.concept_id)

    def test_a_life_area_may_parent_a_workspace(self, content: ContentService, root: Path) -> None:
        # core/03 section 30: a course or a certification belongs to a
        # responsibility rather than to a project, and lives under the area's
        # own `Workspaces/`.
        area = content.create_life_area("Education")
        child = content.create_workspace(
            "Northfield", workspace_type="initiative", parent=area.concept_id
        )
        assert str(child.path) == "20_Life/Education/Workspaces/Northfield/workspace.md"
        assert (root / "20_Life/Education/Workspaces/Northfield/workspace.md").is_file()

    def test_a_workspace_under_an_area_records_the_area_as_its_parent(
        self, content: ContentService
    ) -> None:
        area = content.create_life_area("Education")
        child = content.create_workspace(
            "Northfield", workspace_type="initiative", parent=area.concept_id
        )
        assert child.document.frontmatter["parent"] == str(area.concept_id)

    def test_a_workspace_under_an_area_nests_further(self, content: ContentService) -> None:
        # A course under the degree it belongs to: the area only decides where
        # the top of the lineage sits, not how deep it may go.
        area = content.create_life_area("Education")
        degree = content.create_workspace(
            "Northfield", workspace_type="initiative", parent=area.concept_id
        )
        course = content.create_workspace(
            "CS 201", workspace_type="project", parent=degree.concept_id
        )
        assert str(course.path) == (
            "20_Life/Education/Workspaces/Northfield/Workspaces/CS-201/workspace.md"
        )

    def test_creating_the_same_workspace_twice_is_refused(self, content: ContentService) -> None:
        content.create_workspace("BezaCore", workspace_type="organization")
        with pytest.raises(ConceptCreationError, match="already exists"):
            content.create_workspace("BezaCore", workspace_type="organization")

    def test_sections_appear_on_first_use(self, content: ContentService, root: Path) -> None:
        created = content.create_workspace("BezaCore", workspace_type="organization")
        content.ensure_section(created.concept_id, "Goals")
        assert (root / "10_Workspaces" / "BezaCore" / "Goals").is_dir()
        assert not (root / "10_Workspaces" / "BezaCore" / "Tasks").exists()


class TestCreationDoesNotRepairTheRestOfTheVault:
    """Creating a document writes only what creating it changed.

    If creation called the whole-vault navigation sweep, every unrelated drift
    would be repaired inside the request that created one document.

    The rule this pins is not about navigation. Creating a thing may write what
    creating it changed, and `repair --apply` is the verb that asks for the
    sweep. A vault write nobody asked for is the failure whatever it writes.
    """

    def test_an_unrelated_stale_index_is_untouched(
        self, content: ContentService, root: Path
    ) -> None:
        # A real concept, so the directory is navigable and its index is
        # genuinely a candidate. `create_knowledge` writes no navigation, which
        # is what leaves the drift there for the next pass to find.
        content.create_knowledge("Hybrid Retrieval")
        stale = root / "30_Knowledge" / "Notes" / "index.md"
        stale.write_text("# Notes\n\n- [Nothing](nothing.md)\n")
        before = stale.stat().st_mtime_ns

        content.create_workspace("BezaCore", workspace_type="organization")

        assert stale.read_text() == "# Notes\n\n- [Nothing](nothing.md)\n"
        assert stale.stat().st_mtime_ns == before

    def test_its_own_index_and_its_parent_are_written(
        self, content: ContentService, root: Path
    ) -> None:
        content.create_workspace("BezaCore", workspace_type="organization")

        own = (root / "10_Workspaces" / "BezaCore" / "index.md").read_text()
        parent = (root / "10_Workspaces" / "index.md").read_text()
        assert "<!-- never4ga:navigation -->" in own
        assert "(BezaCore/index.md)" in parent

    def test_a_child_workspace_reaches_every_ancestor(
        self, content: ContentService, root: Path
    ) -> None:
        parent = content.create_workspace("BezaCore", workspace_type="organization")
        content.create_workspace("Never4gA", workspace_type="product", parent=parent.concept_id)

        workspaces = root / "10_Workspaces" / "BezaCore" / "Workspaces"
        assert "(Never4gA/index.md)" in (workspaces / "index.md").read_text()
        assert "(Workspaces/index.md)" in (workspaces.parent / "index.md").read_text()


class TestKnowledgeAndEntities:
    def test_a_knowledge_note_lands_flat_in_notes(
        self, content: ContentService, root: Path
    ) -> None:
        created = content.create_knowledge("Hybrid Retrieval")
        assert str(created.path) == "30_Knowledge/Notes/hybrid-retrieval.md"
        assert (root / "30_Knowledge" / "Notes" / "hybrid-retrieval.md").is_file()

    def test_knowledge_stays_flat_however_many_notes_exist(
        self, content: ContentService, root: Path
    ) -> None:
        for title in ("Python Packaging", "Theology of Work", "Boiler Maintenance"):
            content.create_knowledge(title)
        notes = root / "30_Knowledge" / "Notes"
        assert [p for p in notes.iterdir() if p.is_dir()] == []

    def test_a_person_needs_no_per_type_folder(self, content: ContentService, root: Path) -> None:
        # A life area stays flat. `20_Life/` groups by area of responsibility,
        # never by what kind of person somebody is.
        content.create_life_area("Relationships")
        created = content.create_concept(
            "person", "Ada Lovelace", in_directory=VaultPath.parse("20_Life/Relationships")
        )
        assert str(created.path) == "20_Life/Relationships/ada-lovelace.md"
        assert [p for p in (root / "20_Life" / "Relationships").iterdir() if p.is_dir()] == []

    def test_a_map_lands_in_maps(self, content: ContentService) -> None:
        created = content.create_map("Artificial Intelligence")
        assert str(created.path) == "30_Knowledge/Maps/artificial-intelligence.md"

    def test_a_life_area_gets_an_area_manifest_not_an_index_concept(
        self, content: ContentService, root: Path
    ) -> None:
        created = content.create_life_area("Health")
        assert str(created.path) == "20_Life/Health/area.md"
        index_text = (root / "20_Life" / "Health" / "index.md").read_text(encoding="utf-8")
        assert parse_document(index_text).frontmatter is None

    def test_creating_a_duplicate_note_is_refused(self, content: ContentService) -> None:
        content.create_knowledge("Hybrid Retrieval")
        with pytest.raises(ConceptCreationError, match="already exists"):
            content.create_knowledge("Hybrid Retrieval")

    def test_a_reserved_filename_is_refused(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="reserved"):
            content.create_knowledge("index")

    def test_an_unsafe_title_is_made_into_a_safe_filename(self, content: ContentService) -> None:
        created = content.create_knowledge("What/Why: notes?")
        # A removed character leaves the boundary it stood for, rather than
        # fusing the words either side of it into `whatwhy`.
        assert str(created.path) == "30_Knowledge/Notes/what-why-notes.md"
        # The title itself is human text and is kept verbatim.
        assert created.document.frontmatter["title"] == "What/Why: notes?"


class TestIdentityAcrossTheVault:
    def test_every_concept_has_a_distinct_identity(self, content: ContentService) -> None:
        first = content.create_knowledge("One")
        second = content.create_knowledge("Two")
        assert first.concept_id != second.concept_id

    def test_identity_survives_a_move_on_disk(
        self, content: ContentService, initialized: FileSystemMarkdownStore, root: Path
    ) -> None:
        created = content.create_knowledge("Hybrid Retrieval")
        source = root / "30_Knowledge" / "Notes" / "hybrid-retrieval.md"
        source.rename(root / "00_Inbox" / "Renamed.md")

        initialized.refresh()
        found = initialized.get(created.concept_id)
        assert found is not None
        assert str(found.path) == "00_Inbox/Renamed.md"
        assert found.frontmatter["id"] == str(created.concept_id)


class TestDoctorFindsRealProblems:
    def test_a_duplicate_uuid_is_reported(
        self,
        content: ContentService,
        files: FileSystemVaultFileStore,
        initialized: FileSystemMarkdownStore,
        root: Path,
    ) -> None:
        created = content.create_knowledge("Original")
        source = root / "30_Knowledge" / "Notes" / "original.md"
        (root / "30_Knowledge" / "Notes" / "copy.md").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )

        initialized.refresh()
        diagnosis = Doctor(files, initialized).diagnose()
        assert not diagnosis.healthy
        duplicates = [f for f in diagnosis.errors if f.code == "duplicate_id"]
        assert {str(f.path) for f in duplicates} == {
            "30_Knowledge/Notes/original.md",
            "30_Knowledge/Notes/copy.md",
        }
        assert str(created.concept_id) in duplicates[0].message

    def test_a_misplaced_document_is_reported(
        self,
        content: ContentService,
        files: FileSystemVaultFileStore,
        initialized: FileSystemMarkdownStore,
        root: Path,
    ) -> None:
        workspace = content.create_workspace("BezaCore", workspace_type="organization")
        content.ensure_section(workspace.concept_id, "Decisions")
        note = content.create_knowledge("Wandering Note")
        source = root / "30_Knowledge" / "Notes" / "wandering-note.md"
        source.rename(root / "10_Workspaces" / "BezaCore" / "Decisions" / "wandering-note.md")

        initialized.refresh()
        diagnosis = Doctor(files, initialized).diagnose()
        assert "misplaced_document" in {f.code for f in diagnosis.errors}
        assert note.concept_id is not None

    def test_a_nested_knowledge_folder_is_reported(
        self,
        files: FileSystemVaultFileStore,
        initialized: FileSystemMarkdownStore,
        content: ContentService,
        root: Path,
    ) -> None:
        created = content.create_knowledge("Nested")
        source = root / "30_Knowledge" / "Notes" / "nested.md"
        target = root / "30_Knowledge" / "Notes" / "Programming" / "Python"
        target.mkdir(parents=True)
        source.rename(target / "nested.md")

        initialized.refresh()
        diagnosis = Doctor(files, initialized).diagnose()
        assert "nested_flat_area" in {f.code for f in diagnosis.errors}
        assert created.concept_id is not None

    def test_a_broken_relation_target_is_reported(
        self,
        content: ContentService,
        files: FileSystemVaultFileStore,
        initialized: FileSystemMarkdownStore,
        root: Path,
    ) -> None:
        content.create_knowledge("Source")
        source = root / "30_Knowledge" / "Notes" / "source.md"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "---\n# Source",
                "relations:\n"
                "  - type: depends_on\n"
                "    target: 0198d71c-f0ad-71bd-949a-9276f9ce4e84\n"
                "---\n# Source",
            ),
            encoding="utf-8",
        )
        initialized.refresh()
        diagnosis = Doctor(files, initialized).diagnose()
        assert "unresolved_relation_target" in {f.code for f in diagnosis.errors}

    def test_an_uninitialized_directory_is_diagnosed_not_crashed_on(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        diagnosis = Doctor(
            FileSystemVaultFileStore(empty), FileSystemMarkdownStore(empty)
        ).diagnose()
        assert not diagnosis.healthy
        assert "no_vault_identity" in {f.code for f in diagnosis.errors}
        assert diagnosis.vault_id is None
        assert diagnosis.concept_count == 0

    def test_findings_carry_a_repair_hint(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        diagnosis = Doctor(
            FileSystemVaultFileStore(empty), FileSystemMarkdownStore(empty)
        ).diagnose()
        assert all(finding.repair_hint for finding in diagnosis.findings)

    def test_doctor_changes_nothing(
        self, files: FileSystemVaultFileStore, initialized: FileSystemMarkdownStore, root: Path
    ) -> None:
        # core/02 sections 23 and 32: report, never repair.
        before = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
        Doctor(files, initialized).diagnose()
        after = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
        assert before == after


class TestSeverityDiscipline:
    def test_warnings_do_not_make_a_vault_unhealthy(
        self, content: ContentService, files: FileSystemVaultFileStore, root: Path
    ) -> None:
        content.create_knowledge("Note", domains=["invented_domain"])
        documents = FileSystemMarkdownStore(root)
        diagnosis = Doctor(files, documents).diagnose()
        assert diagnosis.healthy
        assert "unregistered_domain" in {f.code for f in diagnosis.warnings}

    def test_severity_is_reported_per_finding(
        self, content: ContentService, files: FileSystemVaultFileStore, root: Path
    ) -> None:
        content.create_knowledge("Note", tags=["NotLowercase"])
        diagnosis = Doctor(files, FileSystemMarkdownStore(root)).diagnose()
        tag_findings = [f for f in diagnosis.findings if f.code == "unnormalised_tag"]
        assert tag_findings
        assert tag_findings[0].severity is Severity.WARNING


class TestNamingConventionOnDisk:
    """The file naming convention, verified against a real vault."""

    def test_nothing_init_creates_has_a_space_in_its_name(
        self, initialized: object, root: Path
    ) -> None:
        offenders = [str(p.relative_to(root)) for p in root.rglob("*") if " " in p.name]
        assert offenders == []

    def test_every_file_init_creates_is_lowercase(self, initialized: object, root: Path) -> None:
        # Files are lowercase; directories may carry case.
        offenders = [
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and p.name != p.name.lower() and p.name not in TOOL_CONVENTION_FILENAMES
        ]
        assert offenders == []

    def test_conventional_filenames_keep_their_capitals(
        self, initialized: object, root: Path
    ) -> None:
        # README.md is a convention in its own right, and its reach is the
        # point: it stays capitalized inside the vault too.
        assert (root / "50_System/Integrations/Obsidian/README.md").is_file()

    def test_the_structural_directories_keep_their_case(
        self, initialized: object, root: Path
    ) -> None:
        assert (root / "30_Knowledge" / "Notes").is_dir()
        assert (root / "50_System" / "Templates").is_dir()
        assert (root / "00_Inbox").is_dir()

    def test_nothing_the_services_create_has_a_space_in_its_name(
        self, content: ContentService, root: Path
    ) -> None:
        parent = content.create_workspace("Beza Core", workspace_type="organization")
        content.create_workspace(
            "Intelli Grace", workspace_type="product", parent=parent.concept_id
        )
        content.create_knowledge("Hybrid Retrieval")
        content.create_map("Artificial Intelligence")
        content.create_life_area("Home Maintenance")

        offenders = [str(p.relative_to(root)) for p in root.rglob("*") if " " in p.name]
        assert offenders == []

    def test_the_human_title_keeps_its_spaces_and_case(self, content: ContentService) -> None:
        # core/02 section 7.3: human text stays human text.
        created = content.create_knowledge("Hybrid Retrieval")
        assert created.document.frontmatter["title"] == "Hybrid Retrieval"
        assert str(created.path) == "30_Knowledge/Notes/hybrid-retrieval.md"

    def test_a_workspace_directory_keeps_the_titles_case(
        self, content: ContentService, root: Path
    ) -> None:
        content.create_workspace("BezaCore", workspace_type="organization")
        assert (root / "10_Workspaces" / "BezaCore").is_dir()

    def test_underscores_the_author_typed_are_left_alone(self, content: ContentService) -> None:
        # The author uses `_` to separate fields; that is theirs.
        created = content.create_knowledge("green eggs and ham_dr seuss_1985")
        assert str(created.path) == ("30_Knowledge/Notes/green-eggs-and-ham_dr-seuss_1985.md")


class TestDoctorReportsLeakedConnectionSecrets:
    """core/03 section 16: a token in the vault is a finding, never a repair."""

    def _write_connection(self, root: Path, **extra: str) -> Path:
        directory = root / "50_System" / "Integrations"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "work-openproject.md"
        lines = [
            "---",
            "type: integration",
            "id: 01a03428-7d75-703a-8b55-58b8d820bbb7",
            "schema: never4ga/0.1",
            'title: "Work OpenProject"',
            'created_at: "2026-08-25T12:00:00Z"',
            "connection: work_openproject",
            "provider: openproject",
            "base_url: https://pm.example.dev",
            "project_ref: never4ga",
            *[f"{key}: {value}" for key, value in extra.items()],
            "---",
            "",
            "# Work OpenProject",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_a_clean_connection_is_not_a_finding(
        self,
        files: FileSystemVaultFileStore,
        initialized: FileSystemMarkdownStore,
        root: Path,
    ) -> None:
        self._write_connection(root)
        initialized.refresh()
        diagnosis = Doctor(files, initialized).diagnose()
        assert [f for f in diagnosis.findings if f.code == "connection.secret_in_vault"] == []

    def test_a_token_in_the_vault_is_an_error(
        self,
        files: FileSystemVaultFileStore,
        initialized: FileSystemMarkdownStore,
        root: Path,
    ) -> None:
        path = self._write_connection(root, api_token="hunter2-do-not-print")
        initialized.refresh()
        diagnosis = Doctor(files, initialized).diagnose()

        leaks = [f for f in diagnosis.errors if f.code == "connection.secret_in_vault"]
        assert len(leaks) == 1
        assert str(leaks[0].path) == "50_System/Integrations/work-openproject.md"
        assert "api_token" in leaks[0].message
        # Naming the value in a finding would re-leak it into logs and terminals.
        assert "hunter2-do-not-print" not in leaks[0].message
        # Reported, not repaired: the file is exactly as the user wrote it.
        assert "hunter2-do-not-print" in path.read_text(encoding="utf-8")
