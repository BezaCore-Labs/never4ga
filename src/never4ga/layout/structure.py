"""The canonical vault structure (core/01).

The six roots are deliberately stable; extension happens *inside* them rather
than by inventing new top-level topical roots (core/01 section 1). Two filenames
are reserved for OKF navigation and history, and semantic containers therefore
carry a separate manifest concept (core/01 sections 2, 4 and 13).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from enum import StrEnum
from typing import Any, Final

from never4ga.domain.document import VaultPath

__all__ = [
    "ARCHIVE_SECTIONS",
    "AREA_MANIFEST",
    "CHILD_WORKSPACE_DIRECTORY",
    "HOME",
    "INBOX_RESERVED_NAMES",
    "INBOX_SCRATCHPAD_NAME",
    "KNOWLEDGE_DIRECTORIES",
    "RESERVED_INDEX",
    "RESERVED_LOG",
    "ROOT_INDEX",
    "SYSTEM_DIRECTORIES",
    "SYSTEM_MANIFEST",
    "TOOL_CONVENTION_FILENAMES",
    "WORKSPACE_BASE_DIRECTORIES",
    "WORKSPACE_MANIFEST",
    "DocumentRole",
    "FlatArea",
    "VaultRoot",
    "archived_counterpart",
    "flat_area_of",
    "foreign_names",
    "is_foreign_format",
    "is_foreign_note",
    "is_life_area_content",
    "life_area_directory_of",
    "registered_foreign_material",
    "role_of",
    "workspace_directory_of",
    "workspace_section_of",
]


class VaultRoot(StrEnum):
    """The six stable roots of core/01 section 1.

    The numeric prefixes are part of the name: they keep the roots in a
    deliberate order in any file browser, Obsidian included.
    """

    INBOX = "00_Inbox"
    WORKSPACES = "10_Workspaces"
    LIFE = "20_Life"
    KNOWLEDGE = "30_Knowledge"
    SYSTEM = "50_System"
    ARCHIVE = "90_Archive"


#: Reserved for OKF progressive-disclosure navigation (core/01 section 4). Only
#: the bundle-root one may carry frontmatter (core/02 section 4).
RESERVED_INDEX: Final = "index.md"

#: Reserved for OKF chronological directory history (core/01 section 13). A
#: workspace activity record lives in ``Logs/`` and must not use this name.
RESERVED_LOG: Final = "log.md"

#: The Inbox scratchpad: one durable file for thoughts too small to be a
#: note. `capture` writes a file per thought, which is right for a paragraph
#: and heavy for a line -- eight of them make eight files to read back and
#: eight to clear.
INBOX_SCRATCHPAD_NAME: Final = "scratchpad.md"

#: The Inbox's furniture, as opposed to its contents. Nothing here is a
#: *pending* item: navigation and history are permanent, and the scratchpad is
#: drained rather than processed, so listing it would put an item that never
#: leaves in a pile whose whole purpose is to reach zero.
#:
#: Here rather than beside either reader, because both the signal provider and
#: the service that clears the pile need the same answer, and a listing that
#: disagreed with the pack would send a session to file something it cannot see.
INBOX_RESERVED_NAMES: Final = frozenset({RESERVED_INDEX, RESERVED_LOG, INBOX_SCRATCHPAD_NAME})

#: Semantic manifests, kept separate from ``index.md`` so that OKF navigation
#: semantics survive (core/01 section 4).
WORKSPACE_MANIFEST: Final = "workspace.md"
AREA_MANIFEST: Final = "area.md"

ROOT_INDEX: Final = VaultPath.parse(RESERVED_INDEX)
HOME: Final = VaultPath.parse("home.md")
SYSTEM_MANIFEST: Final = VaultPath.parse(f"{VaultRoot.SYSTEM}/system.md")

#: The Domain Registry (core/02 sections 9.1 and 22): the controlled domain
#: vocabulary as a vault document, so the system describes its own vocabulary
#: and curating it means editing Markdown rather than Python.
DOMAIN_REGISTRY: Final = VaultPath.parse(f"{VaultRoot.SYSTEM}/Schemas/domain-registry.md")

#: The system manifest's record of which top-level directories and loose
#: top-level Markdown notes `init` found already there and registered as
#: foreign material (core/01 section 1). Names, not paths: they are always
#: top-level. This is
#: the first form of the foreign-format registry core/02 section 3.3 calls
#: "future", and it lives in the manifest so it survives a reindex and travels
#: with the vault.
FOREIGN_MATERIAL_FIELD: Final = "foreign_material"

#: Where child workspaces physically live (core/01 section 6).
CHILD_WORKSPACE_DIRECTORY: Final = "Workspaces"

#: A workspace's log root. Named because it has two meanings that differ only
#: by depth (core/01 section 13): a dated record lives in `Logs/<YYYY>/` and a
#: closed year's roll-up directly in `Logs/`.
LOGS_DIRECTORY: Final = "Logs"

#: core/01 section 6 and core/03 section 2. Only those actually needed exist on
#: disk; a workspace is not required to carry the whole set.
WORKSPACE_BASE_DIRECTORIES: Final = (
    "Context",
    "Goals",
    "Plans",
    "Tasks",
    "Decisions",
    "Research",
    "Resources",
    LOGS_DIRECTORY,
    "Assets",
    CHILD_WORKSPACE_DIRECTORY,
)

KNOWLEDGE_DIRECTORIES: Final = ("Notes", "Maps", "Assets")

#: core/01 section 10. Extensible by stable semantic responsibility.
SYSTEM_DIRECTORIES: Final = (
    "Standards",
    "Schemas",
    "Templates",
    "Skills",
    "Agents",
    "Documentation",
    "Integrations",
)

#: core/01 section 11. Created only when used.
ARCHIVE_SECTIONS: Final = ("Workspaces", "Life", "Knowledge", "Entities", "System")

#: Filenames whose capitalization is a convention outside Never4gA. A tool,
#: a forge or a reader keys on the exact name, so the "files are lowercase"
#: naming rule does not apply to them -- anywhere, including inside a vault.
TOOL_CONVENTION_FILENAMES: Final = frozenset(
    {
        "README.md",
        "LICENSE",
        "LICENSE.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "AGENTS.md",
        "CLAUDE.md",
        "SKILL.md",
        "Makefile",
        "Dockerfile",
    }
)

#: System directories whose contents are governed by another standard or tool:
#: Agent Skills, template files, agent configuration (core/02 sections 2.2 and
#: 3.3). Everything inside them is foreign-format, however deeply nested.
FOREIGN_FORMAT_DIRECTORIES: Final = (
    f"{VaultRoot.SYSTEM}/Templates",
    f"{VaultRoot.SYSTEM}/Skills",
    f"{VaultRoot.SYSTEM}/Agents",
)

#: `50_System/Integrations/<Name>.md` is an `integration` concept (core/02
#: section 21.19); anything in a subdirectory beneath it belongs to the tool the
#: integration describes and "is not automatically an integration concept".
INTEGRATIONS_DIRECTORY: Final = f"{VaultRoot.SYSTEM}/Integrations"


class DocumentRole(StrEnum):
    """What a Markdown file is, decided by where it sits and what it is called.

    The distinction matters because reserved OKF files must not be validated,
    indexed or rewritten as ordinary concepts (core/02 section 3.2).
    """

    #: The bundle entry point. The one ``index.md`` allowed frontmatter.
    ROOT_INDEX = "root_index"
    #: Reserved navigation. Carries no ordinary concept frontmatter.
    DIRECTORY_INDEX = "directory_index"
    #: Reserved OKF directory history.
    DIRECTORY_LOG = "directory_log"
    #: Governed by another standard or tool -- a Skill, a template, an Obsidian
    #: view. Never4gA reads and writes these but never rewrites them as concepts
    #: (core/02 sections 2.2 and 3.3).
    FOREIGN_FORMAT = "foreign_format"
    #: Everything else, including ``workspace.md``, ``area.md`` and ``home.md``.
    CONCEPT = "concept"


class FlatArea(StrEnum):
    """Directories whose contents must not grow a topical folder hierarchy.

    core/01 section 8 forbids ``Programming/Python/`` under ``Notes/``; section 9
    forbids per-type folders under ``Records/``. Classification is done with
    metadata, links and Maps instead.
    """

    KNOWLEDGE_NOTES = "30_Knowledge/Notes"
    KNOWLEDGE_MAPS = "30_Knowledge/Maps"


def is_foreign_format(path: VaultPath) -> bool:
    """Is this file governed by another standard or tool?

    core/02 section 3.3 requires such files to live in a registered
    integration/system location, and forbids consumers from silently rewriting
    them as Never4gA concepts.
    """
    prefix = "/".join(path.segments[:-1])
    if any(
        prefix == directory or prefix.startswith(f"{directory}/")
        for directory in FOREIGN_FORMAT_DIRECTORIES
    ):
        return True
    # Directly inside Integrations/ is an `integration` concept; deeper is the
    # tool's own material.
    return prefix.startswith(f"{INTEGRATIONS_DIRECTORY}/")


def role_of(path: VaultPath) -> DocumentRole:
    """Classify a Markdown path by its OKF role."""
    if path == ROOT_INDEX:
        return DocumentRole.ROOT_INDEX
    if path.name == RESERVED_INDEX:
        return DocumentRole.DIRECTORY_INDEX
    if path.name == RESERVED_LOG:
        return DocumentRole.DIRECTORY_LOG
    if is_foreign_format(path):
        return DocumentRole.FOREIGN_FORMAT
    return DocumentRole.CONCEPT


#: The top-level files Never4gA reserves at the vault root (core/01 sections
#: 2, 4 and 13). A writer's note by one of these names is never foreign
#: material, because the vault root already gives the name a meaning.
RESERVED_ROOT_NAMES: Final = (RESERVED_INDEX, HOME.name, RESERVED_LOG)


def foreign_names(names: Iterable[str]) -> frozenset[str]:
    """The names that can be foreign material: single segments that are not roots.

    A name is a top-level directory or a loose top-level Markdown note. A
    root is Never4gA's own structure however the manifest describes it, a
    name with a separator, or a dot name, is not something `init` could have
    registered, and a reserved root file is Never4gA's. Dropping them here is
    what keeps a corrupt manifest from turning a root's prose, or the root
    index, into a pile indexed by path.
    """
    roots = {str(root) for root in VaultRoot}
    return frozenset(
        name
        for name in names
        if name
        and "/" not in name
        and "\\" not in name
        and not name.startswith(".")
        and name not in roots
        and name not in RESERVED_ROOT_NAMES
    )


def registered_foreign_material(manifest: Mapping[str, Any]) -> frozenset[str]:
    """The top-level names the system manifest registers (core/01 section 1).

    ``manifest`` is the frontmatter of `50_System/system.md`. The
    registration is the exemption, never the location: a directory or a
    loose note nobody registered is judged like anywhere else, and `doctor`
    says so.
    """
    recorded = manifest.get(FOREIGN_MATERIAL_FIELD) or ()
    if isinstance(recorded, str) or not isinstance(recorded, Iterable):
        return frozenset()
    return foreign_names(str(name) for name in recorded)


def is_foreign_note(path: VaultPath, names: Collection[str]) -> bool:
    """Would a walk of the registered ``names`` read ``path`` as a note?

    A registered loose note itself, or Markdown under a registered directory
    and not in a dot directory. **A pile's own
    `index.md` or `log.md` is one**: the two names are
    reserved in Never4gA's structure (core/01 sections 4 and 13), and a pile
    is not that structure -- it is the writer's, and their `Projects/index.md`
    is usually the note that names everything in the folder. Adopting one out
    of the pile is what has to find it a name that is not reserved.
    """
    return (
        path.segments[0] in foreign_names(names)
        and path.name.endswith(".md")
        and not any(segment.startswith(".") for segment in path.segments)
    )


def _names_a_document(segments: tuple[str, ...], index: int) -> bool:
    """Is the segment at ``index`` a Markdown file rather than a directory?

    A vault path carries no filesystem knowledge, so a trailing ``.md`` is what
    separates `10_Workspaces/index.md` from a workspace directory.
    """
    return index == len(segments) - 1 and segments[index].endswith(".md")


def archived_counterpart(directory: VaultPath) -> VaultPath | None:
    """Where a workspace directory's contents sit once they are archived.

    ``10_Workspaces/<Name>/Decisions`` archives to
    ``90_Archive/Workspaces/<Name>/Decisions``: the path below the root is kept
    whole, so an archived record is found beside where it used to be (core/01
    section 11). ``None`` for a directory outside ``10_Workspaces/``, which
    has no counterpart defined.
    """
    segments = directory.segments
    if segments[0] != VaultRoot.WORKSPACES or len(segments) < 2:
        return None
    return VaultPath((VaultRoot.ARCHIVE, CHILD_WORKSPACE_DIRECTORY, *segments[1:]))


def workspace_directory_of(path: VaultPath) -> VaultPath | None:
    """The innermost workspace directory containing ``path``, if any.

    Workspaces nest through ``Workspaces/`` (core/01 section 6), so a path inside
    a child workspace belongs to the child, not the parent. Archived workspaces
    under ``90_Archive/Workspaces/`` resolve too: archiving detaches a workspace
    from active work without destroying its structure (core/01 section 11).

    A life area has a ``Workspaces/`` of its own (core/01 section 7): a
    bounded pursuit that belongs to a responsibility rather than to a project.
    It resolves exactly as a child workspace does. The area is
    never itself a workspace, so unlike a workspace's own ``Workspaces/`` the
    container has nothing above it to fall back to.
    """
    segments = path.segments
    if segments[0] == VaultRoot.WORKSPACES:
        cursor = 1
    elif segments[:2] == (VaultRoot.ARCHIVE, CHILD_WORKSPACE_DIRECTORY):
        cursor = 2
    elif (
        segments[0] == VaultRoot.LIFE
        and len(segments) > 3
        and segments[2] == CHILD_WORKSPACE_DIRECTORY
    ):
        cursor = 3
    else:
        return None

    if len(segments) <= cursor or _names_a_document(segments, cursor):
        # `10_Workspaces/index.md` is the root's own navigation file, not a
        # workspace called "index.md".
        return None

    innermost = cursor
    while cursor + 2 < len(segments) and segments[cursor + 1] == CHILD_WORKSPACE_DIRECTORY:
        if _names_a_document(segments, cursor + 2):
            break
        cursor += 2
        innermost = cursor
    return VaultPath(segments[: innermost + 1])


def workspace_section_of(path: VaultPath) -> str | None:
    """The workspace directory ``path`` sits in -- ``Goals``, ``Logs``, and so on.

    Profile directories are reported alongside the base set: core/01 section 6
    allows ``<profile extensions>/``, and a caller validating placement needs to
    see the name to decide whether a profile permits it.
    """
    workspace = workspace_directory_of(path)
    if workspace is None:
        return None
    depth = len(workspace.segments)
    if len(path.segments) <= depth + 1:
        # The manifest, the index, or the workspace directory itself.
        return None
    return path.segments[depth]


def life_area_directory_of(path: VaultPath) -> VaultPath | None:
    """The life-area directory containing ``path`` (core/01 section 7)."""
    segments = path.segments
    if segments[0] != VaultRoot.LIFE or len(segments) < 3:
        return None
    return VaultPath(segments[:2])


def is_life_area_content(path: VaultPath) -> bool:
    """Is ``path`` an area's own working material (core/01 section 7)?

    core/01 section 7 gives ``20_Life/<Area>/`` the "extend inside on first use"
    allowance a workspace has, so this is a subtree rule: grouping is allowed
    when it mirrors the structure of the thing being practised —
    ``gardening/seed-catalog/``, ``reading/classics/war-and-peace/``. Section 8's ban
    on invented taxonomies is about ``30_Knowledge/Notes/`` and does not reach
    here.

    The area's own ``Workspaces/`` is excluded. Content there belongs to a
    bounded pursuit and is judged against that workspace's sections, so folding
    it in would make a goal misfiled into a course's ``Research/`` read as
    perfectly good area material. The manifest and the two reserved OKF
    filenames are excluded for the same reason they are everywhere else: they
    are not material.
    """
    if life_area_directory_of(path) is None:
        return False
    if workspace_directory_of(path) is not None:
        return False
    return path.name not in (AREA_MANIFEST, RESERVED_INDEX, RESERVED_LOG)


def flat_area_of(path: VaultPath) -> FlatArea | None:
    """Which flat area ``path`` falls inside, if any.

    Reporting the area is all this layer does. Whether a nested path *inside* one
    is an error is a placement question, answered against the schema.
    """
    prefix = "/".join(path.segments[:2])
    for area in FlatArea:
        if prefix == area.value and len(path.segments) > 2:
            return area
    return None
