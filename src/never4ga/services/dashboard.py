"""Generating the workspace dashboard's embedded views.

`details/obsidian-experience.md` §5 makes `workspace.md` both manifest and
dashboard. A dashboard grows a view for each kind of content the workspace
actually has, and a workspace without that content does not carry the view.
A fixed set of views would leave most types reachable only through the folder
tree, and would show empty tables for the rest.

**Presence is by type, attributed to the innermost workspace.** The views
filter on ``type`` and ``workspace == this.id``, so the honest emission rule is
the same test the view itself will run: emit the view when a concept of that
type belongs to this workspace. A section folder that exists but holds nothing
gets no view, for the reason navigation does not list empty directories: a
table over nothing is the defect, not the feature. Child workspaces are the one
indirection: a child's *manifest* marks the parent as having children, and
nothing else a child holds ever counts toward the parent.

**The views live in a managed block, exactly as navigation does.** Views baked
into the body at creation could never be added to a workspace that already
exists. A delimited block inside `workspace.md` is derivable content in a file a
person also writes -- the repository pointer's shape
(`details/agent-instruction-layering.md` sections 6 and 7) -- so prose outside
the markers is preserved byte for byte, `doctor` can compare the block to the
corpus, and the same refresh passes that keep navigation current keep the
dashboard current: each creating verb within its lineage, `init` and
`repair --apply` as the sweep.

The marker functions are deliberately this module's own rather than shared
with :mod:`never4ga.services.navigation`: the splice rules differ (navigation
absorbs stale link bullets; a dashboard absorbs nothing, because a base embed
somebody hand-wrote is content, not drift), and coupling the two so the
common thirty lines exist once would let a navigation rule change rewrite
workspace manifests.

Column and sort choices are curated per type rather than derived from the
registry, because they are presentation judgements: the registry says
`course_assignment` carries `unit`, `due` and `grade`; only a person decides
that what is due sorts by ``due`` and what is graded shows ``grade``. A test
holds the table to the registry so a new workspace-homed type cannot ship
without a view or a deliberate exemption.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.layout import WORKSPACE_MANIFEST, workspace_directory_of
from never4ga.ports.vault_files import VaultFileStore

__all__ = [
    "BEGIN",
    "EMBEDS",
    "END",
    "block_for",
    "current_block",
    "matches",
    "missing",
    "outdated",
    "presence_of",
    "refresh",
    "spliced",
]

#: The delimiters. HTML comments, as navigation's are: Markdown renders nothing
#: for them and Obsidian hides them, so the block reads as ordinary content.
BEGIN: Final = "<!-- never4ga:views -->"
END: Final = "<!-- /never4ga:views -->"

#: A view's first column names the document, and a file name is not its name.
#:
#: A file name is a slug; a decision's name is the sentence it settles, and
#: that is what `title` holds.
#:
#: `file.asLink` is load-bearing: a bare `title` property renders as text and
#: the column stops being a way into the document. The fallback keeps a row
#: legible for anything with no title -- a foreign file, or one mid-edit.
_DOC_FORMULA: Final = 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'


@dataclass(frozen=True, slots=True)
class _View:
    """One table inside an embed: what it lists and how it orders it."""

    name: str
    columns: tuple[str, ...] = ()
    #: View-level filters, beyond the embed's own type and scope.
    filters: tuple[str, ...] = ()
    sort: tuple[tuple[str, str], ...] = ()
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class _Embed:
    """One `## heading` plus its ```base``` block, emitted when the type is present."""

    heading: str
    type_name: str
    #: What the first column is called for a reader.
    column: str
    views: tuple[_View, ...]
    #: The frontmatter field compared to `this.id`. Children are found by
    #: `parent`; everything else names its workspace.
    scope: str = "workspace"
    #: Base-level filters beyond type and scope.
    extra_filters: tuple[str, ...] = ()

    def render(self) -> str:
        lines = ["```base", "filters:", "  and:"]
        lines.append(f'    - type == "{self.type_name}"')
        lines.append(f"    - {self.scope} == this.id")
        lines.extend(f"    - {clause}" for clause in self.extra_filters)
        lines.append("formulas:")
        lines.append(f"  doc: {_DOC_FORMULA}")
        lines.append("properties:")
        lines.append("  formula.doc:")
        lines.append(f"    displayName: {self.column}")
        lines.append("views:")
        for view in self.views:
            lines.append("  - type: table")
            lines.append(f"    name: {view.name}")
            if view.filters:
                lines.append("    filters:")
                lines.append("      and:")
                lines.extend(f"        - {clause}" for clause in view.filters)
            lines.append("    order:")
            lines.append("      - formula.doc")
            lines.extend(f"      - {column}" for column in view.columns)
            if view.sort:
                lines.append("    sort:")
                for prop, direction in view.sort:
                    lines.append(f"      - property: {prop}")
                    lines.append(f"        direction: {direction}")
            if view.limit is not None:
                lines.append(f"    limit: {view.limit}")
        lines.append("```")
        return "\n".join(lines)


#: Every view the dashboard can carry, in the order a dashboard shows them.
#: `test_every_workspace_homed_type_has_a_view` holds this table to the
#: registry.
EMBEDS: Final[tuple[_Embed, ...]] = (
    _Embed(
        "Goals",
        "goal",
        "Goal",
        (_View("Goals", ("lifecycle", "target_date")),),
    ),
    _Embed(
        "Plans",
        "plan",
        "Plan",
        (_View("Plans", ("lifecycle",)),),
    ),
    _Embed(
        "Decisions",
        "decision",
        "Decision",
        (_View("Decisions", ("lifecycle", "authority"), sort=(("lifecycle", "ASC"),)),),
    ),
    _Embed(
        "Context",
        "context",
        "Document",
        (_View("Context", ("description",)),),
    ),
    _Embed(
        "Tasks",
        "task",
        "Task",
        (_View("Tasks", ("lifecycle", "priority", "due_date"), sort=(("due_date", "ASC"),)),),
    ),
    _Embed(
        "Research",
        "research_note",
        "Note",
        (_View("Research", ("lifecycle",)),),
    ),
    # The two course embeds: units in unit order, and graded work split into
    # what is still owed and what came back with a grade.
    _Embed(
        "Units",
        "course_unit",
        "Unit",
        (_View("Units", ("unit", "lifecycle", "due"), sort=(("unit", "ASC"),)),),
    ),
    _Embed(
        "Graded Work",
        "course_assignment",
        "Assignment",
        (
            _View(
                "What is due",
                ("unit", "lifecycle", "due"),
                filters=('lifecycle != "graded"',),
                sort=(("due", "ASC"),),
            ),
            _View(
                "Graded",
                ("unit", "grade"),
                filters=('lifecycle == "graded"',),
                sort=(("unit", "ASC"),),
            ),
        ),
    ),
    _Embed(
        "Resources",
        "resource",
        "Resource",
        (_View("Resources", ("description",)),),
    ),
    _Embed(
        "Standards",
        "standard",
        "Standard",
        (_View("Standards", ("authority",)),),
    ),
    _Embed(
        # Named for the type rather than for `Architecture/`, which is only
        # one of the folders `documentation` is at home in; a workspace's own
        # `Documentation/` is another, and a heading naming one folder would
        # hide the other.
        "Documentation",
        "documentation",
        "Document",
        (_View("Documentation", ("description",)),),
    ),
    _Embed(
        # A runbook is listed apart from documentation for the reason it is a
        # separate type: when something is broken, "what do I do when this
        # breaks" is a different need from "how is this built". A
        # dashboard that merged them would undo the distinction at exactly the
        # moment it matters.
        "Runbooks",
        "runbook",
        "Runbook",
        (_View("Runbooks", ("description", "lifecycle")),),
    ),
    _Embed(
        # How each phase of a plan was done. Listed by phase and status, so the
        # one in progress is found at a glance.
        "Walkthroughs",
        "walkthrough",
        "Walkthrough",
        (_View("Walkthroughs", ("phase", "lifecycle")),),
    ),
    _Embed(
        "Recent Activity",
        "activity_log",
        "Log",
        (
            _View(
                "Recent activity",
                ("occurred_at",),
                sort=(("occurred_at", "DESC"),),
                limit=15,
            ),
        ),
        # core/01 section 13: a rolled-up year moves to 90_Archive but keeps
        # naming this workspace, so scope by folder or archived history comes
        # back into the dashboard it just left.
        extra_filters=("file.inFolder(this.file.folder)",),
    ),
    _Embed(
        "Child Workspaces",
        "workspace",
        "Workspace",
        (_View("Child workspaces", ("workspace_type", "lifecycle")),),
        scope="parent",
    ),
)


def presence_of(concepts: Sequence[StoredDocument]) -> dict[VaultPath, set[str]]:
    """Which types each workspace holds, attributed to the innermost workspace.

    A concept in a child workspace is the child's alone -- a course's units
    never surface on its parent programme's dashboard. The one indirection is the child's
    own manifest: it marks the *parent* as having children (the `workspace`
    entry), because that is the fact the Child Workspaces view renders, and it
    never marks the child, whose manifest is itself rather than its child.
    """
    present: dict[VaultPath, set[str]] = {}
    for document in concepts:
        directory = workspace_directory_of(document.path)
        if directory is None:
            continue
        type_name = document.frontmatter.get("type")
        if not isinstance(type_name, str):
            continue
        if document.path.segments == (*directory.segments, WORKSPACE_MANIFEST):
            present.setdefault(directory, set())
            parent = workspace_directory_of(VaultPath(directory.segments[:-1]))
            if parent is not None:
                present.setdefault(parent, set()).add("workspace")
            continue
        present.setdefault(directory, set()).add(type_name)
    return present


def block_for(present: Collection[str]) -> str:
    """The managed block for a workspace holding these types, delimiters included."""
    lines = [BEGIN, ""]
    emitted = [embed for embed in EMBEDS if embed.type_name in present]
    if emitted:
        for embed in emitted:
            lines.append(f"## {embed.heading}")
            lines.append("")
            lines.append(embed.render())
            lines.append("")
    else:
        # Said out loud rather than left blank, as navigation says it: an
        # empty workspace is a fact, and a silent block reads like a defect.
        lines.append("_Views appear here as content arrives._")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def current_block(text: str) -> str | None:
    """The managed block as it stands in ``text``, or ``None`` if there is none."""
    start = text.find(BEGIN)
    if start == -1:
        return None
    end = text.find(END, start)
    if end == -1:
        # An opened block with no close is not a block; treating it as one
        # would let a truncated file swallow everything after it.
        return None
    return text[start : end + len(END)]


def matches(text: str, block: str) -> bool:
    """Whether the file already carries exactly this block. Byte equality."""
    return current_block(text) == block


def spliced(text: str, block: str) -> str:
    """``text`` with the managed block replaced, or appended if it has none.

    Everything outside the markers is preserved byte for byte -- including a
    hand-written base embed, which is content somebody chose, not drift. The
    only exception is a stray unpaired marker, dropped so a file never ends up
    with two opening markers.
    """
    existing = current_block(text)
    if existing is not None:
        return text.replace(existing, block, 1)
    kept: list[str] = []
    for line in text.splitlines():
        if line.strip() in {BEGIN, END}:
            continue
        kept.append(line)
    remaining = "\n".join(kept)
    if not remaining.strip():
        return f"{block}\n"
    return f"{remaining.rstrip()}\n\n{block}\n"


def _manifests(concepts: Sequence[StoredDocument]) -> list[tuple[VaultPath, VaultPath, str]]:
    """(workspace directory, manifest path, correct block) for every workspace."""
    present = presence_of(concepts)
    found = []
    for document in concepts:
        directory = workspace_directory_of(document.path)
        if directory is None or document.frontmatter.get("type") != "workspace":
            continue
        if document.path.segments != (*directory.segments, WORKSPACE_MANIFEST):
            continue
        found.append((directory, document.path, block_for(present.get(directory, set()))))
    return sorted(found, key=lambda entry: str(entry[0]))


def outdated(files: VaultFileStore, concepts: Sequence[StoredDocument]) -> list[VaultPath]:
    """Manifests whose block exists and no longer says what the corpus does."""
    found = []
    for _, manifest, block in _manifests(concepts):
        text = files.read_text(manifest)
        if text is None or current_block(text) is None:
            continue
        if not matches(text, block):
            found.append(manifest)
    return found


def missing(files: VaultFileStore, concepts: Sequence[StoredDocument]) -> list[VaultPath]:
    """Manifests with no views block at all, reported once with a count.

    A vault written before the block existed is one fact about the vault, not
    one warning per workspace -- the same shape as `navigation_is_missing`.
    """
    found = []
    for _, manifest, _block in _manifests(concepts):
        text = files.read_text(manifest)
        if text is not None and current_block(text) is None:
            found.append(manifest)
    return found


def refresh(
    files: VaultFileStore,
    concepts: Sequence[StoredDocument],
    *,
    within: Collection[VaultPath] | None = None,
) -> int:
    """Rewrite every dashboard whose block has drifted. Returns how many.

    ``within`` bounds which manifests may be written, exactly as navigation's
    refresh is bounded: a creation passes its lineage and can only touch
    the workspaces it actually changed, while `init` and `repair --apply`
    sweep. What is *rendered* is still computed from the whole corpus.

    A manifest whose file is missing is skipped rather than invented: the
    manifest is a concept with identity and frontmatter, and writing one is
    creation's job, not a refresh's.
    """
    written = 0
    for directory, manifest, block in _manifests(concepts):
        if within is not None and directory not in within:
            continue
        text = files.read_text(manifest)
        if text is None or matches(text, block):
            continue
        files.write_text(manifest, spliced(text, block))
        written += 1
    return written
