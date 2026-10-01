"""Vault initialization (core/01, details/obsidian-experience.md section 3).

``never4ga init`` creates the seven stable roots, the OKF bundle index, the
human dashboard, the system manifest whose id is the vault identity, a template
set and the starter Obsidian pack.

It is idempotent and never destructive. An existing file is left exactly as it
is -- Never4gA does not own content it did not write, and re-running init on a
vault someone has been using must not undo their work (core/05 section 16,
core/09 section 11).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.layout import (
    DOMAIN_REGISTRY,
    FOREIGN_MATERIAL_FIELD,
    HOME,
    INBOX_SCRATCHPAD_NAME,
    KNOWLEDGE_DIRECTORIES,
    RESERVED_INDEX,
    ROOT_INDEX,
    SYSTEM_DIRECTORIES,
    SYSTEM_MANIFEST,
    VaultRoot,
)
from never4ga.ports.document_store import DocumentStore
from never4ga.ports.vault_files import VaultFileStore
from never4ga.schema import DOMAIN_VALUES
from never4ga.services import dashboard, scaffold
from never4ga.services.authoring import (
    NEVER4GA_ACTOR,
    Clock,
    build_concept,
    utc_now,
    with_updated_generation,
)
from never4ga.services.navigation import refresh as navigation_refresh
from never4ga.services.skill_library import SEED_MANIFEST, template_seed_manifest

__all__ = ["ForeignDirectory", "InitializationResult", "VaultInitializer"]

_OBSIDIAN = f"{VaultRoot.SYSTEM}/Integrations/Obsidian"


@dataclass(frozen=True, slots=True)
class ForeignDirectory:
    """A top-level directory that was already there and is not Never4gA's.

    `core/01` section 1: the user's, held beside the roots untouched. ``files`` is what
    `init` counted under it, so the report can say what it found.
    """

    directory: str
    files: int


@dataclass(frozen=True, slots=True)
class InitializationResult:
    """What ``init`` did, and what it deliberately left alone."""

    vault_id: ConceptId
    created: tuple[VaultPath, ...] = field(default=())
    preserved: tuple[VaultPath, ...] = field(default=())
    #: Files `init` owns and rewrote. Today only the system manifest, when the
    #: foreign material it records changed.
    updated: tuple[VaultPath, ...] = field(default=())
    #: Every top-level directory registered as foreign material, whether this
    #: run found it or an earlier one did.
    foreign_material: tuple[ForeignDirectory, ...] = field(default=())

    @property
    def already_initialized(self) -> bool:
        return SYSTEM_MANIFEST in self.preserved


class VaultInitializer:
    """Creates the canonical vault structure."""

    def __init__(
        self,
        files: VaultFileStore,
        documents: DocumentStore,
        *,
        now: Clock = utc_now,
        actor: str = NEVER4GA_ACTOR,
    ) -> None:
        self._files = files
        self._documents = documents
        self._now = now
        self._actor = actor
        self._created: list[VaultPath] = []
        self._preserved: list[VaultPath] = []
        self._updated: list[VaultPath] = []

    def initialize(self, title: str = "Never4gA Vault") -> InitializationResult:
        self._created = []
        self._preserved = []
        self._updated = []

        # Before anything is created: what is foreign is what was there
        # first, and a directory this run makes must never be mistaken for it.
        foreign = self._find_foreign_material()
        already_there = tuple(self._files.iter_paths())
        self._create_directories()
        self._create_navigation()
        vault_id = self._create_system_manifest(title)
        registered = self._register_foreign_material(foreign)
        self._create_domain_registry()
        self._create_home(title)
        self._create_templates()
        self._create_skills()
        self._create_obsidian_pack()
        self._seed_navigation()

        return InitializationResult(
            vault_id=vault_id,
            created=tuple(self._created),
            preserved=self._left_alone(already_there),
            updated=tuple(self._updated),
            foreign_material=registered,
        )

    def _left_alone(self, already_there: tuple[VaultPath, ...]) -> tuple[VaultPath, ...]:
        """Every file that was there before this run and is still as it was.

        `preserved` counts everything that was already there, including a pile
        of the user's own notes, not only the files `init` would have written
        itself. A file this run rewrote -- the system manifest, when the foreign
        material it records changes -- also stays in it. Dot directories are the
        tool's, and the file store never walks them.
        """
        preserved = list(self._preserved)
        seen = set(preserved)
        for path in already_there:
            if path not in seen and path not in self._created:
                preserved.append(path)
                seen.add(path)
        return tuple(preserved)

    # -- steps ------------------------------------------------------------

    def _find_foreign_material(self) -> tuple[ForeignDirectory, ...]:
        """Every top-level directory that is not a root (`core/01` section 1).

        Dot directories never reach here: the file store leaves them out, and
        `.obsidian/` is the tool's, not the user's. A top-level *file* is not
        foreign material either -- `index.md` and `home.md` are reserved, and a
        loose README or note is left exactly where it is without a record,
        because a registry of directories is what the later steps read.
        """
        roots = {str(root) for root in VaultRoot}
        found: list[ForeignDirectory] = []
        for directory in sorted(self._files.iter_directories()):
            if len(directory.segments) != 1 or directory.segments[0] in roots:
                continue
            prefix = directory.segments[:1]
            files = sum(1 for path in self._files.iter_paths() if path.segments[:1] == prefix)
            found.append(ForeignDirectory(directory.segments[0], files))
        return tuple(found)

    def _register_foreign_material(
        self, found: tuple[ForeignDirectory, ...]
    ) -> tuple[ForeignDirectory, ...]:
        """Record the foreign directories in the manifest, once each.

        The union with what an earlier `init` recorded: a directory registered
        then and emptied since is still the user's, and un-registering it is
        not this verb's to do. The manifest is rewritten only when the list
        changes, so a second `init` over the same pile touches nothing.
        """
        manifest = self._documents.get_by_path(SYSTEM_MANIFEST)
        assert manifest is not None  # created or preserved just above
        recorded = [str(name) for name in manifest.frontmatter.get(FOREIGN_MATERIAL_FIELD) or []]
        names = sorted({*recorded, *(one.directory for one in found)})
        if names != recorded:
            self._documents.put(
                with_updated_generation(
                    manifest,
                    actor=self._actor,
                    now=self._now,
                    **{FOREIGN_MATERIAL_FIELD: names},
                )
            )
            if SYSTEM_MANIFEST not in self._created:
                self._updated.append(SYSTEM_MANIFEST)
        counted = {one.directory: one.files for one in found}
        return tuple(ForeignDirectory(name, counted.get(name, 0)) for name in names)

    def _seed_navigation(self) -> None:
        """Put the generated block into every index this run created.

        Last, because it describes what the other steps wrote. Without it a
        freshly initialised vault reports `navigation_is_outdated` on every
        index it had just created. The scaffolds keep their headings and their
        orientation prose; the list of links is the generator's, here as
        everywhere.

        The workspace dashboard block is the same kind of thing: a fresh vault
        holds no workspaces yet, so this is usually a no-op, but an `init` over
        an existing tree must leave both generated regions saying what the
        corpus says.
        """
        corpus = list(self._documents.iter_documents())
        navigation_refresh(self._files, corpus)
        dashboard.refresh(self._files, corpus)

    def _create_directories(self) -> None:
        """The seven roots plus the subdirectories core/01 names.

        Archive subfolders are not created: core/01 section 11 says to create
        them only when used, and an empty `90_Archive/Workspaces/` invites
        premature filing.
        """
        directories = [
            *(VaultPath.parse(str(root)) for root in VaultRoot),
            *(VaultPath.parse(f"{VaultRoot.KNOWLEDGE}/{name}") for name in KNOWLEDGE_DIRECTORIES),
            *(VaultPath.parse(f"{VaultRoot.SYSTEM}/{name}") for name in SYSTEM_DIRECTORIES),
            VaultPath.parse(f"{_OBSIDIAN}/Bases"),
            VaultPath.parse(f"{_OBSIDIAN}/Documentation"),
        ]
        for directory in directories:
            if not self._files.is_directory(directory):
                self._files.ensure_directory(directory)
                self._created.append(directory)

    def _create_navigation(self) -> None:
        """The OKF bundle index and each directory's navigation file."""
        self._write_file(ROOT_INDEX, scaffold.ROOT_INDEX)
        for directory, body in scaffold.ROOT_INDEXES.items():
            self._write_file(VaultPath.parse(f"{directory}/{RESERVED_INDEX}"), body)
        self._write_file(
            VaultPath.parse(f"{VaultRoot.INBOX}/{INBOX_SCRATCHPAD_NAME}"), scaffold.INBOX_SCRATCHPAD
        )

    def _create_system_manifest(self, title: str) -> ConceptId:
        """core/05 section 6: this document's id is the vault identity."""
        existing = self._documents.get_by_path(SYSTEM_MANIFEST)
        if existing is not None:
            self._preserved.append(SYSTEM_MANIFEST)
            return existing.concept_id

        manifest = build_concept(
            concept_type="system_manifest",
            title=title,
            path=SYSTEM_MANIFEST,
            actor=self._actor,
            now=self._now,
            description="Canonical manifest for this Never4gA vault.",
            body=scaffold.SYSTEM_MANIFEST_BODY,
            profiles=["never4ga/core/0.1"],
        )
        self._documents.put(manifest)
        self._created.append(SYSTEM_MANIFEST)
        return manifest.concept_id

    def _create_domain_registry(self) -> None:
        """core/02 section 22: the vault describes its own domain vocabulary.

        Seeded with the bootstrap values and never touched again: the registry
        is the user's to curate (section 9.1), so a re-init that rewrote it
        would throw away exactly the work it exists to hold.
        """
        if self._documents.get_by_path(DOMAIN_REGISTRY) is not None:
            self._preserved.append(DOMAIN_REGISTRY)
            return

        registry = build_concept(
            concept_type="registry",
            title="Domain Registry",
            path=DOMAIN_REGISTRY,
            actor=self._actor,
            now=self._now,
            description=(
                "The controlled domain vocabulary (core/02 section 9.1). "
                "Curated by hand: add a value here to register it."
            ),
            body=scaffold.DOMAIN_REGISTRY_BODY,
            registry="domains",
            values=list(DOMAIN_VALUES),
        )
        self._documents.put(registry)
        self._created.append(DOMAIN_REGISTRY)

    def _create_home(self, title: str) -> None:
        if self._documents.get_by_path(HOME) is not None or self._files.exists(HOME):
            self._preserved.append(HOME)
            return
        self._documents.put(
            build_concept(
                concept_type="dashboard",
                title=title,
                path=HOME,
                actor=self._actor,
                now=self._now,
                description="Human-facing entry point for this vault.",
                body=scaffold.HOME_BODY,
            )
        )
        self._created.append(HOME)

    def _create_templates(self) -> None:
        """Concept skeletons, and the record of what they were seeded as.

        The manifest is what lets an improved template reach this vault later.
        It cannot live in a template's own frontmatter: those are
        placeholders a person copies to start a note, so a field added there
        would be copied into every concept made from one.
        """
        for name, content in scaffold.SHIPPED_TEMPLATES.items():
            self._write_file(VaultPath.parse(f"{VaultRoot.SYSTEM}/Templates/{name}"), content)
        self._write_file(
            VaultPath.parse(f"{VaultRoot.SYSTEM}/Templates/{SEED_MANIFEST}"),
            template_seed_manifest(),
        )

    def _create_skills(self) -> None:
        """The eight canonical Skills (core/04 section 22).

        The vault is canonical for Skills and the wheel does not ship them: a
        Skill is user-controlled source (core/04 section 20), so `init` seeds
        them and an edit is honoured exactly as an edited template is. What
        deploys them to a client is `never4ga adapters sync`, which never reads
        from anywhere but here.
        """
        for name, content in scaffold.SKILLS.items():
            self._write_file(VaultPath.parse(f"{VaultRoot.SYSTEM}/Skills/{name}/SKILL.md"), content)

    def _create_obsidian_pack(self) -> None:
        self._write_file(VaultPath.parse(f"{_OBSIDIAN}/README.md"), scaffold.OBSIDIAN_README)
        for name, content in scaffold.BASES.items():
            self._write_file(VaultPath.parse(f"{_OBSIDIAN}/Bases/{name}"), content)

    # -- helpers ----------------------------------------------------------

    def _write_file(self, path: VaultPath, content: str) -> None:
        """Write, unless something is already there.

        The check is the point: a user who edited a template, a Base or their
        own `home.md` keeps it.
        """
        if self._files.exists(path):
            self._preserved.append(path)
            return
        self._files.write_text(path, content)
        self._created.append(path)
