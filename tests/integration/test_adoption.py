"""Adopting Markdown that already exists.

Adoption brings a hand-written document under tracking (details/api-cli-mcp-contract.md
section 3). It infers the type from where the file sits, the reverse of the placement
`concept create` applies. It mints the identity a person cannot write, preserves the
body verbatim, and leaves the file where it is.

An ambiguous inference is refused with the candidates named, never guessed.
Choosing a type is a judgement, which is why `untracked_document` stays out of
`repair --apply`: two people reading the finding would not write the same fix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from never4ga.adapters.filesystem import FileSystemMarkdownStore, FileSystemVaultFileStore
from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.schema import ValidationLevel, validate_document
from never4ga.services import ConceptCreationError, ContentService, VaultInitializer

WORKSPACE_DIRECTORY = "10_Workspaces/BezaCore"


def fixed_clock() -> datetime:
    return datetime(2026, 9, 7, 19, 0, 0, tzinfo=UTC)


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
def content(files: FileSystemVaultFileStore, documents: FileSystemMarkdownStore) -> ContentService:
    VaultInitializer(files, documents, now=fixed_clock).initialize("Test Vault")
    documents.refresh()
    service = ContentService(files, documents, now=fixed_clock, actor="claude-code/test")
    service.create_workspace("BezaCore", workspace_type="organization")
    documents.refresh()
    return service


def hand_written(root: Path, relative: str, text: str) -> VaultPath:
    """A file the way a person writes one: outside every Never4gA verb."""
    absolute = root / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(text, encoding="utf-8")
    return VaultPath.parse(relative)


class TestAdoptingWhereTheFolderDecides:
    def test_the_type_is_inferred_from_where_the_file_sits(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "# Latency Notes\n\nMeasured on the LAN.\n",
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["type"] == "research_note"

    def test_the_file_stays_exactly_where_it_is(self, content: ContentService, root: Path) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "# Latency Notes\n\nbody\n",
        )
        adopted = content.adopt_concept(path)
        assert adopted.path == path
        assert (root / str(path)).is_file()

    def test_the_body_is_preserved_verbatim(self, content: ContentService, root: Path) -> None:
        body = "# Latency Notes\n\nMeasured on the LAN.\n\n- 49 ms scoped\n- 1,310 ms unscoped\n"
        path = hand_written(root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", body)
        content.adopt_concept(path)
        text = (root / str(path)).read_text(encoding="utf-8")
        assert text.endswith(body)
        assert text.startswith("---\n")

    def test_identity_is_minted_and_canonical(self, content: ContentService, root: Path) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        adopted = content.adopt_concept(path)
        assert ConceptId.parse(str(adopted.concept_id)) == adopted.concept_id
        assert adopted.document.frontmatter["created_at"] == "2026-09-07T19:00:00Z"
        assert adopted.document.frontmatter["generated"]["by"] == "claude-code/test"

    def test_the_workspace_field_is_derived_from_the_path(
        self, content: ContentService, root: Path, documents: FileSystemMarkdownStore
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        adopted = content.adopt_concept(path)
        manifest = documents.get_by_path(VaultPath.parse(f"{WORKSPACE_DIRECTORY}/workspace.md"))
        assert manifest is not None
        assert adopted.document.frontmatter["workspace"] == str(manifest.concept_id)

    def test_a_lifecycle_vocabulary_starts_at_its_first_value(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["lifecycle"] == "active"

    def test_the_adopted_document_validates_at_strict(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        adopted = content.adopt_concept(path)
        report = validate_document(
            adopted.path, adopted.document.frontmatter, level=ValidationLevel.STRICT
        )
        assert not report.errors

    def test_it_is_reachable_by_identity_afterwards(
        self, content: ContentService, root: Path, documents: FileSystemMarkdownStore
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        adopted = content.adopt_concept(path)
        documents.refresh()
        found = documents.get(adopted.concept_id)
        assert found is not None
        assert found.path == path

    def test_a_flat_knowledge_note_is_inferred(self, content: ContentService, root: Path) -> None:
        path = hand_written(root, "30_Knowledge/Notes/uuidv7.md", "# UUIDv7\n\nTime-ordered.\n")
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["type"] == "knowledge"

    def test_the_reason_says_what_the_folder_decided(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        adopted = content.adopt_concept(path)
        assert "adopted where it sits" in adopted.placement_reason
        assert "research_note" in adopted.placement_reason


class TestTheTitle:
    def test_the_first_heading_is_the_title(self, content: ContentService, root: Path) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "# Latency on the LAN\n\nbody\n",
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["title"] == "Latency on the LAN"

    def test_without_a_heading_the_filename_serves(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "just prose\n"
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["title"] == "latency notes"

    def test_a_title_field_overrides_the_heading(self, content: ContentService, root: Path) -> None:
        # A supplied field is the caller saying what the title is, so it wins
        # over the heading.
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "# Latency on the LAN\n\nbody\n",
        )
        adopted = content.adopt_concept(path, fields={"title": "LAN Latency"})
        assert adopted.document.frontmatter["title"] == "LAN Latency"
        assert adopted.document.body == "# Latency on the LAN\n\nbody\n"

    def test_a_title_field_overrides_a_hand_written_title(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "---\ntitle: The Writer's Own Title\n---\n# A Different Heading\n",
        )
        adopted = content.adopt_concept(path, fields={"title": "Named at Adoption"})
        assert adopted.document.frontmatter["title"] == "Named at Adoption"

    def test_a_description_field_overrides_a_hand_written_one(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "---\ndescription: What the writer said.\n---\n# Latency Notes\n",
        )
        adopted = content.adopt_concept(path, fields={"description": "What adoption said."})
        assert adopted.document.frontmatter["description"] == "What adoption said."


class TestPartialFrontmatterIsHonored:
    def test_a_declared_type_wins_over_inference(self, content: ContentService, root: Path) -> None:
        # Strategy/ is ambiguous between standard and resource; the writer
        # already said which.
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Strategy/positioning.md",
            "---\ntype: standard\n---\n# Positioning\n",
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["type"] == "standard"

    def test_unknown_fields_survive_adoption(self, content: ContentService, root: Path) -> None:
        # core/02 section 5.1: unknown fields MUST survive a round trip.
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "---\nx_custom: kept\ntags:\n  - latency\n---\n# Latency Notes\n",
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["x_custom"] == "kept"
        assert adopted.document.frontmatter["tags"] == ["latency"]

    @pytest.mark.parametrize("name", ["path", "body", "actor", "now", "concept_type"])
    def test_a_field_named_like_an_assembly_argument_is_only_a_field(
        self, content: ContentService, root: Path, name: str
    ) -> None:
        # core/02 section 5.1 again. These names mean something to the code
        # that assembles a concept and nothing to the schema, so they must not
        # collide with its own arguments.
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            f"---\n{name}: kept\n---\n# Latency Notes\n",
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter[name] == "kept"
        assert adopted.path == path

    @pytest.mark.parametrize("name", ["path", "body", "actor", "now", "concept_type"])
    def test_a_supplied_field_named_like_an_assembly_argument_is_only_a_field(
        self, content: ContentService, root: Path, name: str
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        adopted = content.adopt_concept(path, fields={name: "supplied"})
        assert adopted.document.frontmatter[name] == "supplied"
        assert adopted.document.body == "# Latency Notes\n"

    def test_a_hand_written_title_is_kept(self, content: ContentService, root: Path) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "---\ntitle: The Writer's Own Title\n---\n# A Different Heading\n",
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["title"] == "The Writer's Own Title"

    def test_a_hand_written_created_at_is_kept(self, content: ContentService, root: Path) -> None:
        # The writer's timestamp is a fact about the content; minting over it
        # would fabricate the very field adoption exists to make truthful.
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            '---\ncreated_at: "2026-09-01T08:00:00Z"\n---\n# Latency Notes\n',
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["created_at"] == "2026-09-01T08:00:00Z"


class TestRefusingToGuess:
    def test_an_ambiguous_folder_is_refused_with_the_candidates_named(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Strategy/positioning.md", "# Positioning\n"
        )
        with pytest.raises(ConceptCreationError) as raised:
            content.adopt_concept(path)
        message = str(raised.value)
        assert "resource" in message
        assert "standard" in message

    def test_the_candidates_travel_as_data_not_only_as_a_sentence(
        self, content: ContentService, root: Path
    ) -> None:
        """So a surface offers them as choices rather than parsing English."""
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Strategy/positioning.md", "# Positioning\n"
        )
        with pytest.raises(ConceptCreationError) as raised:
            content.adopt_concept(path)
        assert raised.value.candidates == ("resource", "standard")

    def test_the_refusal_names_no_flag_because_the_service_has_no_surface(
        self, content: ContentService, root: Path
    ) -> None:
        """The service states the fact; each surface phrases its own ask.

        A surface such as the Obsidian plugin has no flags, only a Type field,
        so `--type` in the message would be a wrong instruction there."""
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Strategy/positioning.md", "# Positioning\n"
        )
        with pytest.raises(ConceptCreationError) as raised:
            content.adopt_concept(path)
        assert "--type" not in str(raised.value)

    def test_a_folder_no_type_is_at_home_in_offers_no_candidates(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(root, "30_Knowledge/stray.md", "# Stray\n")
        with pytest.raises(ConceptCreationError) as raised:
            content.adopt_concept(path)
        assert raised.value.candidates == ()

    def test_the_type_argument_resolves_the_ambiguity(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Strategy/positioning.md", "# Positioning\n"
        )
        adopted = content.adopt_concept(path, concept_type="standard")
        assert adopted.document.frontmatter["type"] == "standard"

    def test_a_folder_no_type_is_at_home_in_needs_the_type_named(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(root, "30_Knowledge/stray.md", "# Stray\n")
        with pytest.raises(ConceptCreationError, match="name what it is"):
            content.adopt_concept(path)

    def test_the_type_argument_conflicting_with_declared_frontmatter_is_refused(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Strategy/positioning.md",
            "---\ntype: standard\n---\n# Positioning\n",
        )
        with pytest.raises(ConceptCreationError, match="declares"):
            content.adopt_concept(path, concept_type="resource")

    def test_an_unregistered_type_is_refused(self, content: ContentService, root: Path) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        with pytest.raises(ConceptCreationError, match="not a registered type"):
            content.adopt_concept(path, concept_type="essay")

    def test_a_required_field_never4ga_cannot_know_is_refused_by_name(
        self, content: ContentService, root: Path
    ) -> None:
        # When a logged thing happened is a fact about the content; the same
        # rule that forbids fabricating generated.at forbids inventing it.
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Logs/standup.md", "# Standup\n\nWhat happened.\n"
        )
        with pytest.raises(ConceptCreationError, match="occurred_at"):
            content.adopt_concept(path)

    def test_supplying_the_field_completes_the_adoption(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Logs/standup.md", "# Standup\n\nWhat happened.\n"
        )
        adopted = content.adopt_concept(path, fields={"occurred_at": "2026-09-07T15:00:00Z"})
        assert adopted.document.frontmatter["type"] == "activity_log"


class TestWhatMayNotBeAdopted:
    def test_a_missing_file_is_refused(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="no file"):
            content.adopt_concept(VaultPath.parse(f"{WORKSPACE_DIRECTORY}/Research/absent.md"))

    def test_a_document_that_already_carries_an_id_is_refused(
        self, content: ContentService, root: Path
    ) -> None:
        identity = ConceptId.new()
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/tracked.md",
            f"---\ntype: research_note\nid: {identity}\n---\n# Tracked\n",
        )
        with pytest.raises(ConceptCreationError, match="already"):
            content.adopt_concept(path)

    def test_reserved_navigation_is_refused(self, content: ContentService) -> None:
        with pytest.raises(ConceptCreationError, match="reserved"):
            content.adopt_concept(VaultPath.parse(f"{WORKSPACE_DIRECTORY}/index.md"))

    def test_foreign_format_material_is_refused(self, content: ContentService, root: Path) -> None:
        path = hand_written(root, "50_System/Templates/essay.md", "# Essay\n")
        with pytest.raises(ConceptCreationError, match="foreign"):
            content.adopt_concept(path)

    def test_the_inbox_keeps_its_own_flow(self, content: ContentService, root: Path) -> None:
        path = hand_written(root, "00_Inbox/2026-09-07_thought.md", "a thought\n")
        with pytest.raises(ConceptCreationError, match="inbox resolve"):
            content.adopt_concept(path)

    def test_a_workspace_manifest_points_at_its_own_verb(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(root, "10_Workspaces/Harborview/workspace.md", "# Harborview\n")
        with pytest.raises(ConceptCreationError, match="workspace create"):
            content.adopt_concept(path)

    def test_unparseable_frontmatter_is_refused_rather_than_replaced(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/broken.md",
            "---\n: not yaml [\n---\n# Broken\n",
        )
        with pytest.raises(ConceptCreationError):
            content.adopt_concept(path)


OWNED_FIELD_VALUES: tuple[tuple[str, object], ...] = (
    ("type", "garbage"),
    ("id", str(ConceptId.new())),
    ("schema", "garbage"),
    ("generated", {"by": "someone/1.0", "at": "2026-09-01T08:00:00Z"}),
)


class TestFieldsNever4gAWrites:
    """A supplied owned field is refused, not written or ignored."""

    @pytest.mark.parametrize(("name", "value"), OWNED_FIELD_VALUES)
    def test_an_owned_field_is_refused_and_the_file_is_untouched(
        self, content: ContentService, root: Path, name: str, value: object
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        before = (root / str(path)).read_text(encoding="utf-8")
        with pytest.raises(ConceptCreationError, match=rf"^{name} is written by Never4gA"):
            content.adopt_concept(path, fields={name: value})
        assert (root / str(path)).read_text(encoding="utf-8") == before

    def test_a_supplied_type_points_at_the_argument_that_names_one(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root, f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md", "# Latency Notes\n"
        )
        with pytest.raises(ConceptCreationError, match="--type"):
            content.adopt_concept(path, fields={"type": "research_note"})

    def test_the_file_s_own_owned_fields_are_still_replaced_not_refused(
        self, content: ContentService, root: Path
    ) -> None:
        # The refusal is for what a caller supplies. What a person wrote into
        # the file before adoption is the file, and adoption exists to make it
        # a tracked concept -- schema stated, provenance recorded. (A file that
        # already carries an `id` is a concept, and adoption refuses it whole.)
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            "---\nschema: garbage\ngenerated:\n  by: someone/1.0\n"
            '  at: "2026-09-01T08:00:00Z"\n---\n# Latency Notes\n',
        )
        adopted = content.adopt_concept(path)
        assert adopted.document.frontmatter["schema"] == "never4ga/0.1"
        assert adopted.document.frontmatter["generated"]["by"] != "someone/1.0"

    def test_a_supplied_created_at_wins_over_a_hand_written_one(
        self, content: ContentService, root: Path
    ) -> None:
        path = hand_written(
            root,
            f"{WORKSPACE_DIRECTORY}/Research/latency-notes.md",
            '---\ncreated_at: "2026-09-01T08:00:00Z"\n---\n# Latency Notes\n',
        )
        adopted = content.adopt_concept(path, fields={"created_at": "2026-08-01T08:00:00Z"})
        assert adopted.document.frontmatter["created_at"] == "2026-08-01T08:00:00Z"
