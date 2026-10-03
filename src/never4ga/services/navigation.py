"""Generating the navigation `index.md` files.

`core/01` section 4 reserves `index.md` for OKF progressive disclosure and
section 6 keeps ordinary concept frontmatter out of it. That combination is what
makes it *generatable*: it carries no identity, no relations and no judgement,
so what it should say is derivable from the directory it navigates.

A generated index lists real documents by title, links `.md` files rather than
folders, includes only sections that have content, and stays plain Markdown.
Static folder stubs go stale and link directories that Obsidian does nothing
useful with; generated links work for a reader with or without Obsidian.

**Why a managed block rather than the whole file.** The *navigation* is
derivable, but the file may also carry prose a person wrote. That prose is
orientation rather than navigation, and regenerating wholesale would delete it.
So the generated part lives between two markers and prose is left alone. It is
the same shape as the repository pointer (`details/agent-instruction-layering.md`
sections 6 and 7): a file Never4gA does not wholly own gets a delimited region
it does, and byte equality decides whether it may write there.

**The block absorbs the old list.** Splicing a correct block above a stale one
would leave two sets of navigation in one document, one of them wrong. A line
that is nothing but a Markdown link bullet *is* navigation, and this file is
reserved for navigation, so on the first splice those lines are taken over.
Anything else is prose and survives untouched.

That is also why the block carries :data:`DESCRIPTIONS`. What a directory is
*for* is worth saying, and it is derivable, because a directory's role is fixed
by its name.

Nothing here writes except :func:`refresh`, and nothing calls that on a timer:
`doctor` reports and `repair --apply` performs.

**Limits.**

The vault's root `index.md` is left alone. It is the OKF *bundle* index, with
`okf_version` frontmatter, and it lists the stable roots that `core/01` section 1
fixes. A list that cannot go stale does not need generating.

A directory holding no *concepts* is not listed, even when it holds files.
`Assets/` is the common case: binaries are legitimate vault content but never
become concepts, so a workspace's `Assets/` is absent from its index. Listing it
would mean walking the filesystem for every directory on every diagnosis.

**Every directory that holds concepts gets an index.** A section link points at
`Name/index.md` when that file exists and at `Name/` otherwise, and when the
file is missing Obsidian resolves neither. `doctor` reports missing indexes once
with a count, and `repair --apply` creates them. Creating a file is a larger
write than rewriting a block, which is why it stays behind `--apply`. The
content is wholly derived, so a vault that lost every index would rebuild them
identically.

The set is computed from what *will* have an index once the pass finishes, never
from what has one now. Otherwise the first pass links `Decisions/`, only a
second pass writes `Decisions/index.md`, and a freshly repaired vault still
reports a finding.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.layout import RESERVED_INDEX, RESERVED_LOG
from never4ga.ports.vault_files import VaultFileStore

__all__ = [
    "BEGIN",
    "DESCRIPTIONS",
    "END",
    "NavigationEntry",
    "block_for",
    "current_block",
    "entries_for",
    "lineage_of",
    "matches",
    "missing_indexes",
    "navigable_directories",
    "outdated",
    "refresh",
    "spliced",
]

#: The delimiters. HTML comments because Markdown renders nothing for them and
#: Obsidian hides them, so the block reads as ordinary content to a person.
BEGIN: Final = "<!-- never4ga:navigation -->"
END: Final = "<!-- /never4ga:navigation -->"

#: What a directory is *for*, by the role its name carries.
#:
#: Not per-vault knowledge and not derivable from the contents: `Logs/` holds
#: activity history in every workspace there has ever been. The scaffolds `init`
#: seeds carry the same sentences, which is why they are worth generating rather
#: than leaving as prose somebody has to keep true -- a description of a fixed
#: role does not go stale, and a hand-written list of directories does.
DESCRIPTIONS: Final[dict[str, str]] = {
    "00_Inbox": "Unfiled capture.",
    "10_Workspaces": "Bounded contexts with their own goals and history.",
    "20_Life": "Ongoing life areas.",
    "30_Knowledge": "Durable reusable knowledge.",
    "50_System": "Vault machinery.",
    "90_Archive": "Detached material, kept out of context.",
    "Architecture": "Specifications and design.",
    "Assets": "Supporting binaries.",
    "Context": "Current orientation and constraints.",
    "Decisions": "Durable decisions.",
    "Goals": "Desired outcomes.",
    "Integrations": "External system configuration.",
    "Logs": "Activity and session history.",
    "Maps": "Curated navigation and synthesis.",
    "Notes": "Durable reusable knowledge. Semantically flat.",
    "Plans": "Active and historical plans.",
    "Records": "One document per canonical thing.",
    "Research": "Investigations belonging to this context.",
    "Resources": "Supporting material.",
    "Skills": "Portable agent Skills.",
    "Standards": "Rules that bind work here.",
    "Tasks": "Work items held in the vault.",
    "Templates": "Concept skeletons.",
    "Workspaces": "Nested bounded contexts.",
}

#: Files that are navigation or history rather than content, and so are never
#: listed *in* navigation (`core/01` sections 4 and 5).
_RESERVED: Final = frozenset({RESERVED_INDEX, RESERVED_LOG})

#: A line that is a link bullet and nothing more, optionally with the trailing
#: description the seeded scaffolds carry.
_LINK_BULLET: Final = re.compile(r"^\s*[-*]\s*\[[^\]]*\]\([^)]*\)\s*(?:[-:\u2013\u2014]\s*.*)?$")


@dataclass(frozen=True, slots=True)
class NavigationEntry:
    """One line of navigation: where it goes and what to call it."""

    target: str
    title: str
    #: What the thing is for, when that is knowable from its role. Documents
    #: carry none: a document's own title is the description.
    description: str = ""
    #: A directory's entry points at its own `index.md` when it has one, and at
    #: the directory otherwise. Kept apart from :attr:`target` so the caller can
    #: group without parsing the link back out.
    is_directory: bool = False


def entries_for(
    directory: VaultPath | None,
    concepts: Sequence[StoredDocument],
    *,
    indexed_directories: Iterable[VaultPath] = (),
) -> list[NavigationEntry]:
    """What the index at ``directory`` should link to.

    ``None`` is the vault root. Documents directly inside come first, by title;
    then the subdirectories that hold anything, each pointing at its own
    `index.md` where one exists.

    Derived entirely from ``concepts``, which the caller already holds -- the
    diagnosis loads the corpus once and this is one of the things it pays for.
    A subdirectory with no concepts in it is left out rather than listed as an
    empty link.
    """
    prefix = () if directory is None else directory.segments
    depth = len(prefix)
    with_index = {tuple(path.segments) for path in indexed_directories}

    here: list[NavigationEntry] = []
    below: dict[str, bool] = {}
    for document in concepts:
        segments = document.path.segments
        if segments[:depth] != prefix or len(segments) == depth:
            continue
        rest = segments[depth:]
        if len(rest) == 1:
            if rest[0] in _RESERVED:
                continue
            here.append(NavigationEntry(rest[0], _title_of(document)))
        else:
            below.setdefault(rest[0], False)

    for name in below:
        below[name] = (*prefix, name, RESERVED_INDEX) in {
            (*path, RESERVED_INDEX) for path in with_index
        } or (*prefix, name) in with_index

    directories = [
        NavigationEntry(
            f"{name}/{RESERVED_INDEX}" if has_index else f"{name}/",
            _humanise(name),
            description=DESCRIPTIONS.get(name, ""),
            is_directory=True,
        )
        for name, has_index in sorted(below.items())
    ]
    return sorted(here, key=lambda entry: entry.title.casefold()) + directories


def block_for(entries: Sequence[NavigationEntry]) -> str:
    """The managed block, delimiters included.

    Two lists rather than one, because a directory and a document are different
    kinds of hop and running them together reads as a pile of links. A section
    with nothing in it is omitted rather than rendered empty.
    """
    lines = [BEGIN, ""]
    documents = [entry for entry in entries if not entry.is_directory]
    directories = [entry for entry in entries if entry.is_directory]

    if directories:
        lines.append("## Sections")
        lines.append("")
        lines.extend(_line(entry) for entry in directories)
        lines.append("")
    if documents:
        lines.append("## Documents")
        lines.append("")
        lines.extend(_line(entry) for entry in documents)
        lines.append("")
    if not directories and not documents:
        # Said out loud rather than left blank: an empty directory is a fact
        # about the vault, and a silent block reads like a bug in the generator.
        lines.append("_Nothing here yet._")
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
        # An opened block with no close is not a block. Treating it as one would
        # let a truncated file swallow everything after it on the next write.
        return None
    return text[start : end + len(END)]


def matches(text: str, block: str) -> bool:
    """Whether the file already carries exactly this block.

    Byte equality, like the repository pointer's check
    (`details/agent-instruction-layering.md` section 7). Anything looser turns
    "somebody edited this" into "close enough", which is the judgement the
    marker exists to avoid making.
    """
    return current_block(text) == block


def spliced(text: str, block: str) -> str:
    """``text`` with the managed block replaced, or inserted if it has none.

    Prose outside the markers is preserved byte for byte. A *link-bullet line*
    is not prose: it is navigation, in a file reserved for navigation, and the
    block now says the same thing correctly -- so on the first splice those
    lines are absorbed rather than left standing above a list that contradicts
    them.

    Once a block exists this only replaces it, and nothing else in the file is
    ever touched again.
    """
    existing = current_block(text)
    if existing is not None:
        return text.replace(existing, block, 1)
    if not text.strip():
        return f"{block}\n"
    kept = _without_link_bullets(text)
    if not kept.strip():
        return f"{block}\n"
    return f"{kept.rstrip()}\n\n{block}\n"


def _without_link_bullets(text: str) -> str:
    """Drop lines that are only a Markdown link bullet, and nothing else.

    ``- [Context](Context/) - Current orientation and constraints.`` goes; a
    paragraph goes nowhere. A blank line left behind by a dropped run is
    collapsed so the prose does not end up separated from its own heading.

    Stray markers go too, for the reason :func:`current_block` refuses to read
    an unterminated block: a file must never end up with two opening markers.
    """
    kept: list[str] = []
    for line in text.splitlines():
        if _LINK_BULLET.match(line):
            continue
        # A stray marker from a truncated or hand-mangled block. Leaving it
        # would give the file two opening markers, and the next read would take
        # the span between the old one and the new close as "the block".
        if line.strip() in {BEGIN, END}:
            continue
        if not line.strip() and kept and not kept[-1].strip():
            continue
        kept.append(line)
    return "\n".join(kept)


def _line(entry: NavigationEntry) -> str:
    link = f"- [{entry.title}]({entry.target})"
    return f"{link} - {entry.description}" if entry.description else link


def _title_of(document: StoredDocument) -> str:
    title = document.frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return _humanise(document.path.segments[-1].removesuffix(".md"))


def _humanise(name: str) -> str:
    """A directory has no title, so its name has to become one."""
    return name.replace("-", " ").replace("_", " ").strip().title()


def navigable_directories(concepts: Sequence[StoredDocument]) -> set[VaultPath]:
    """Every directory that holds a concept, at any depth.

    Every ancestor, not only the immediate parent. `30_Knowledge/` holds no
    concepts of its own -- they live in `Notes/` -- so a rule that looked at
    direct parents alone would skip every directory that is nothing but
    subdirectories, which is most of the roots.
    """
    directories: set[VaultPath] = set()
    for document in concepts:
        for depth in range(1, len(document.path.segments)):
            directories.add(VaultPath(document.path.segments[:depth]))
    return directories


def lineage_of(document: VaultPath) -> set[VaultPath]:
    """Every directory whose navigation a document at ``document`` is part of.

    Its own directory and each ancestor, which is exactly the set a new
    document changes: the directory gains an entry for the document, and each
    ancestor may gain an entry for the subdirectory below it -- the case where
    a directory holds its first concept and so becomes navigable at all.

    It changes nothing else. Passed to :func:`refresh` as ``within``, this is
    the blast radius a creation is allowed to have.
    """
    return {VaultPath(document.segments[:depth]) for depth in range(1, len(document.segments))}


def missing_indexes(files: VaultFileStore, concepts: Sequence[StoredDocument]) -> list[VaultPath]:
    """Directories holding concepts with no `index.md`, in path order.

    Reported so `doctor` can say it once with a count rather than once per
    directory. A vault that has never had per-directory navigation is one fact,
    not one warning per directory.
    """
    return sorted(
        (
            directory
            for directory in navigable_directories(concepts)
            if not files.exists(VaultPath((*directory.segments, RESERVED_INDEX)))
        ),
        key=str,
    )


def outdated(files: VaultFileStore, concepts: Sequence[StoredDocument]) -> list[VaultPath]:
    """Existing indexes whose block no longer matches their directory.

    A directory with *no* index is a different finding, reported once with a
    count by :func:`missing_indexes`. Folding the two together would turn one
    fact -- "this vault has never had per-directory navigation" -- into a
    warning per directory.
    """
    return [index for index, _ in _stale(files, concepts) if files.read_text(index) is not None]


def refresh(
    files: VaultFileStore,
    concepts: Sequence[StoredDocument],
    *,
    within: Collection[VaultPath] | None = None,
) -> int:
    """Rewrite every block that has drifted. Returns how many were written.

    The one loop, called by `init` when it seeds a vault, by `workspace create`
    and `life-area create` when they add a directory, and by `repair --apply`.
    Sharing it means a directory made after `init` gets the same block `init`
    would have written, so a fresh vault reports no finding against itself.

    **``within`` bounds which indexes may be written.** Without it the pass
    sweeps the vault, which is right for `init` -- the vault is new -- and for
    `repair --apply`, where sweeping is the whole verb. It is wrong for a
    creation: every unrelated drift in the vault would ride along inside a
    request that made one file. A creation passes :func:`lineage_of`, and then
    it can only touch what it actually changed.

    What is *listed* is still computed from the whole corpus. Bounding the
    writes must not bound the reading, or a scoped pass would render its own
    ancestors as though the rest of the vault were not there.
    """
    written = 0
    for index, block in _stale(files, concepts, within=within):
        text = files.read_text(index)
        if text is None:
            # A directory that had no index at all. It gets one, opening with
            # its own name: a file that starts with an HTML comment reads as
            # machinery, and the directory's name is the one thing about it
            # that is certainly true.
            heading = _humanise(index.segments[-2]) if len(index.segments) > 1 else "Index"
            files.write_text(index, f"# {heading}\n\n{block}\n")
            written += 1
            continue
        files.write_text(index, spliced(text, block))
        written += 1
    return written


def _stale(
    files: VaultFileStore,
    concepts: Sequence[StoredDocument],
    *,
    within: Collection[VaultPath] | None = None,
) -> list[tuple[VaultPath, str]]:
    """Every index this pass may write that does not say the right thing.

    ``indexed`` is every directory that *will* have an index once this pass
    finishes, not every directory that has one now. Computed from what exists,
    the first pass writes a section link to `Decisions/` and only a second pass
    writes `Decisions/index.md`, so the vault reports a finding immediately
    after being repaired.

    A **scoped** pass has to answer that question differently, because it is no
    longer true that every navigable directory will end up with an index -- it
    creates only the ones in scope. So "will have one" becomes "is in scope, or
    already has one", and a link to a directory this pass will not create still
    points at the directory rather than at a file that is not going to appear.
    The `exists` calls this costs are cheap stats over directories the vault
    already walked; what a scoped pass bounds is writes, not reads.
    """
    directories = navigable_directories(concepts)
    if within is None:
        candidates, indexed = directories, directories
    else:
        candidates = {directory for directory in directories if directory in within}
        indexed = {
            directory
            for directory in directories
            if directory in candidates
            or files.exists(VaultPath((*directory.segments, RESERVED_INDEX)))
        }

    found = []
    for directory in sorted(candidates, key=str):
        index = VaultPath((*directory.segments, RESERVED_INDEX))
        block = block_for(entries_for(directory, concepts, indexed_directories=indexed))
        text = files.read_text(index)
        if text is None or not matches(text, block):
            found.append((index, block))
    if within is None:
        found.extend(_emptied(files, directories))
    return found


def _emptied(files: VaultFileStore, navigable: set[VaultPath]) -> list[tuple[VaultPath, str]]:
    """Generated indexes left in a directory that no longer holds a concept.

    Everything a directory held can move away -- retyped to where its type
    lives, or adopted out of it -- and its index stays behind, linking to files
    that are gone. Only a sweep looks for these, because finding them walks the
    vault; a creation's scoped pass never empties a directory. An index with no
    generated block was written by a person and is not ours to rewrite.
    """
    empty = block_for([])
    found = []
    for path in sorted(files.iter_paths(), key=str):
        if path.name != RESERVED_INDEX or len(path.segments) < 2:
            continue
        if VaultPath(path.segments[:-1]) in navigable:
            continue
        text = files.read_text(path)
        if text is not None and current_block(text) is not None and not matches(text, empty):
            found.append((path, empty))
    return found
