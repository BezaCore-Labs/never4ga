"""FileSystemMarkdownStore -- canonical Markdown on disk.

The v0.1 :class:`~never4ga.ports.document_store.DocumentStore` named by core/05
section 21. Markdown files under a vault root *are* the store; there is no
sidecar database, and nothing here is the sole representation of anything
(core/00 #1 and #3).

Three consequences of "Never4gA is not the only writer" shape this adapter:

**Identity comes from the file, not from a mapping Never4gA keeps.** The
``id`` in frontmatter is authoritative (core/02 section 5.1), so a file the user
moves in Obsidian is still found by its identity after :meth:`refresh`.

**Problems are reported, not resolved.** A duplicate ``id``, a missing one, or
unreadable YAML makes *that* document unavailable and nothing else. core/02
section 32 lists these as maintenance concerns; section 23 requires repair to be
explicit. ``doctor`` reads them through :meth:`problems` and
:meth:`duplicate_ids`.

**Writes are conservative.** Updating a document merges the new frontmatter onto
what is already on disk, so comments, key order and formatting the user owns
survive. A write that changes nothing rewrites nothing.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Collection, Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from never4ga.adapters.filesystem.frontmatter import (
    FrontmatterError,
    parse_document,
    render_document,
)
from never4ga.domain.document import ForeignNote, StoredDocument, UntrackedMarkdown, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.errors import DuplicateConceptIdError, VaultIntegrityError, VaultPathError
from never4ga.layout import DocumentRole, foreign_names, is_foreign_note, role_of
from never4ga.ports.document_store import FileStat, StoreProblem

__all__ = ["FileSystemMarkdownStore", "StoreProblem"]

_MARKDOWN_SUFFIX: Final = ".md"
_ENCODING: Final = "utf-8"


@dataclass(frozen=True, slots=True)
class _Scan:
    """One pass over the vault."""

    by_id: Mapping[ConceptId, VaultPath]
    duplicates: Mapping[ConceptId, tuple[VaultPath, ...]]
    problems: tuple[StoreProblem, ...]


class FileSystemMarkdownStore:
    """A DocumentStore backed by Markdown files under ``root``."""

    def __init__(self, root: Path) -> None:
        if not root.exists():
            raise VaultPathError(f"vault root {root} does not exist")
        if not root.is_dir():
            raise VaultPathError(f"vault root {root} is not a directory")
        self._root = root.resolve()
        self._scan: _Scan | None = None

    @property
    def root(self) -> Path:
        return self._root

    # -- reading ----------------------------------------------------------

    def get(self, concept_id: ConceptId) -> StoredDocument | None:
        scan = self._ensure_scan()
        if concept_id in scan.duplicates:
            locations = ", ".join(str(path) for path in scan.duplicates[concept_id])
            raise DuplicateConceptIdError(
                f"{concept_id} is claimed by more than one document ({locations}); "
                "resolve the duplicate before addressing it by identity"
            )
        path = scan.by_id.get(concept_id)
        return self._read(path) if path is not None else None

    def get_by_path(self, path: VaultPath) -> StoredDocument | None:
        return self._read(path)

    def iter_documents(self) -> Iterator[StoredDocument]:
        for path in self._markdown_paths():
            document = self._read(path)
            if document is not None:
                yield document

    def get_untracked(self, path: VaultPath) -> UntrackedMarkdown | None:
        """The Markdown at ``path`` that is not (yet) a concept."""
        text = self._read_text(path)
        if text is None:
            return None
        try:
            parsed = parse_document(text)
        except FrontmatterError as error:
            raise VaultIntegrityError(
                f"{path} opens a frontmatter block that cannot be read ({error}); "
                "adoption starts from what was written, and a file it cannot "
                "faithfully read is a file it must not rewrite"
            ) from error
        frontmatter = parsed.frontmatter or {}
        if not frontmatter and _opens_a_frontmatter_block(text):
            raise VaultIntegrityError(
                f"{path} opens a frontmatter block that never closes on a line of "
                "its own; close it before adopting"
            )
        if any(not isinstance(key, str) for key in frontmatter):
            raise VaultIntegrityError(
                f"{path} opens a frontmatter block whose keys are not all plain "
                "strings; repair it before adopting"
            )
        if frontmatter.get("id") is not None:
            raise VaultIntegrityError(
                f"{path} already carries an id; it is a concept, and a broken one "
                "is `doctor`'s to report rather than adoption's to overwrite"
            )
        return UntrackedMarkdown(path=path, frontmatter=frontmatter, body=parsed.body)

    # -- writing ----------------------------------------------------------

    def put(self, document: StoredDocument) -> None:
        scan = self._ensure_scan()
        occupant = self._identity_at(document.path)
        if occupant is not None and occupant != document.concept_id:
            raise VaultPathError(
                f"path {document.path} is already occupied by {occupant}; "
                "two documents cannot share one location"
            )

        previous = scan.by_id.get(document.concept_id)
        self._write(document)
        if previous is not None and previous != document.path:
            # A move: identity is preserved, the old location is vacated.
            self._absolute(previous).unlink(missing_ok=True)
        self._scan = None

    def delete(self, concept_id: ConceptId) -> None:
        scan = self._ensure_scan()
        path = scan.by_id.get(concept_id)
        if path is None:
            return
        self._absolute(path).unlink(missing_ok=True)
        self._scan = None

    # -- integrity --------------------------------------------------------

    def refresh(self) -> None:
        """Forget what was learned about the vault and look again.

        Needed after something outside Never4gA moves or edits a file.
        """
        self._scan = None

    def iter_file_stats(self) -> Iterator[FileStat]:
        """Every concept-eligible file, described without being parsed.

        The cheap comparison of `details/data-indexing-maintenance.md` section 13.
        A file that vanishes mid-walk is skipped rather than raised: the vault is
        not Never4gA's alone, and a read command must not fail because the user
        saved in Obsidian at the wrong moment.
        """
        for path in self._markdown_paths():
            try:
                stat = self._absolute(path).stat()
            except OSError:
                continue
            yield FileStat(path=path, size=stat.st_size, modified_at=stat.st_mtime)

    def file_stat(self, path: VaultPath) -> FileStat | None:
        try:
            stat = self._absolute(path).stat()
        except OSError:
            return None
        return FileStat(path=path, size=stat.st_size, modified_at=stat.st_mtime)

    def duplicate_ids(self) -> Mapping[ConceptId, tuple[VaultPath, ...]]:
        """Identities claimed by more than one document (core/02 section 32)."""
        return self._ensure_scan().duplicates

    def problems(self) -> tuple[StoreProblem, ...]:
        """Files that look like concepts but cannot be read as one."""
        return self._ensure_scan().problems

    def untracked(self) -> tuple[VaultPath, ...]:
        """Concept-eligible Markdown that opens no frontmatter block.

        The complement of what :meth:`_markdown_paths` yields: prose the scan
        deliberately leaves out so it cannot rot every staleness comparison,
        surfaced here so `doctor` can report it instead of calling the vault
        healthy around it.
        """
        return tuple(
            path for path in self._concept_eligible_paths() if not self._declares_frontmatter(path)
        )

    # -- foreign material (core/02 section 3.3) ----------------------------

    def iter_foreign_notes(self, directories: Collection[str]) -> Iterator[ForeignNote]:
        """Every note in registered foreign material that is not a concept."""
        for path in self._foreign_paths(directories):
            note = self.get_foreign_note(path)
            if note is not None:
                yield note

    def get_foreign_note(self, path: VaultPath) -> ForeignNote | None:
        """The writer's note as written, or ``None`` for a concept or nothing.

        Never raises on what the writer put in it. Frontmatter this store
        cannot read -- bad YAML, keys that are not strings -- leaves the whole
        file as the body: the note is still worth finding, and `doctor`
        already reports it as untracked. A file that vanishes between the walk
        and the read is simply not there, as :meth:`iter_file_stats` treats it.
        """
        try:
            text = self._read_text(path)
        except OSError, UnicodeDecodeError:
            return None
        if text is None:
            return None
        try:
            parsed = parse_document(text)
        except FrontmatterError:
            return ForeignNote(path=path, frontmatter={}, body=text)
        frontmatter = parsed.frontmatter or {}
        if any(not isinstance(key, str) for key in frontmatter):
            return ForeignNote(path=path, frontmatter={}, body=text)
        if frontmatter and _identity_of(frontmatter) is not None:
            return None
        return ForeignNote(
            path=path, frontmatter=frontmatter, body=parsed.body, body_offset=parsed.body_offset
        )

    def iter_foreign_file_stats(self, directories: Collection[str]) -> Iterator[FileStat]:
        """Every file :meth:`iter_foreign_notes` would read, described without reading it."""
        for path in self._foreign_paths(directories):
            try:
                stat = self._absolute(path).stat()
            except OSError:
                continue
            yield FileStat(path=path, size=stat.st_size, modified_at=stat.st_mtime)

    def _foreign_paths(self, directories: Collection[str]) -> Iterator[VaultPath]:
        """The registered loose notes, and Markdown under the registered directories.

        The same walk :meth:`_concept_eligible_paths` makes, confined to the
        names registered: dot directories and symlinks are not followed, and
        a registered name that is gone yields nothing.
        """
        usable = foreign_names(directories)
        found: list[VaultPath] = []
        for name in sorted(usable):
            top = self._root / name
            if top.is_symlink():
                continue
            if top.is_file():
                loose = VaultPath.parse(name)
                if is_foreign_note(loose, usable):
                    found.append(loose)
                continue
            if not top.is_dir():
                continue
            for directory, subdirectories, filenames in os.walk(top, followlinks=False):
                subdirectories[:] = [
                    child
                    for child in subdirectories
                    if not child.startswith(".") and not (Path(directory) / child).is_symlink()
                ]
                for filename in filenames:
                    if not filename.endswith(_MARKDOWN_SUFFIX) or filename.startswith("."):
                        continue
                    path = self._relative(Path(directory) / filename)
                    if is_foreign_note(path, usable):
                        found.append(path)
        yield from sorted(found)

    # -- internals --------------------------------------------------------

    def _absolute(self, path: VaultPath) -> Path:
        resolved = self._root.joinpath(*path.segments)
        # Defence in depth: VaultPath already rejects `..` and absolute paths,
        # but a symlinked directory could still lead out of the vault.
        if not self._is_inside(resolved):
            raise VaultPathError(f"{path} resolves outside the vault root")
        return resolved

    def _is_inside(self, candidate: Path) -> bool:
        try:
            existing = candidate
            while not existing.exists():
                parent = existing.parent
                if parent == existing:
                    return True
                existing = parent
            return existing.resolve().is_relative_to(self._root)
        except OSError:
            return False

    def _relative(self, absolute: Path) -> VaultPath:
        return VaultPath(absolute.relative_to(self._root).parts)

    def _markdown_paths(self) -> Iterator[VaultPath]:
        """Every concept-eligible Markdown file, in a stable order.

        Dot directories are skipped: `.obsidian`, `.git` and friends are tool
        state, not vault content. Reserved OKF navigation and history files are
        skipped too -- they are not concepts (core/02 section 3.2).

        **Eligibility is intent, not location.** A file whose path allows a
        concept but which never opens a frontmatter block is prose, and prose is
        legitimate vault content that will never be indexed. Counting it here
        would make it permanently "new" in every staleness comparison, and
        `capture` writes exactly such a file.
        A file that *opens* a block stays eligible however badly it parses,
        which is what keeps `unterminated_frontmatter` visible rather than
        quietly reclassifying a broken concept as prose.
        """
        for path in self._concept_eligible_paths():
            if self._declares_frontmatter(path):
                yield path

    def _concept_eligible_paths(self) -> Iterator[VaultPath]:
        """Every Markdown file whose path allows a concept, frontmatter or not."""
        for directory, subdirectories, filenames in os.walk(self._root, followlinks=False):
            subdirectories[:] = sorted(
                name
                for name in subdirectories
                if not name.startswith(".") and not (Path(directory) / name).is_symlink()
            )
            for filename in sorted(filenames):
                if not filename.endswith(_MARKDOWN_SUFFIX) or filename.startswith("."):
                    continue
                path = self._relative(Path(directory) / filename)
                if role_of(path) is DocumentRole.CONCEPT:
                    yield path

    def _declares_frontmatter(self, path: VaultPath) -> bool:
        """Whether the file opens a frontmatter block, read one line deep.

        Cheaper than a parse by design: `iter_file_stats` exists so a staleness
        check never has to read a whole vault, and this keeps that promise while
        making the answer the honest one.
        """
        try:
            with self._absolute(path).open("r", encoding="utf-8") as handle:
                first = handle.readline()
        except OSError, UnicodeDecodeError:
            # Unreadable now is not the same as absent; leave it in so the
            # scan reports rather than silently dropping it.
            return True
        return _opens_a_frontmatter_block(first)

    def _read_text(self, path: VaultPath) -> str | None:
        absolute = self._absolute(path)
        if not absolute.is_file():
            return None
        return absolute.read_text(encoding=_ENCODING)

    def _read(self, path: VaultPath) -> StoredDocument | None:
        """Load one document, or ``None`` when the file is not a canonical concept."""
        if role_of(path) is not DocumentRole.CONCEPT:
            return None
        text = self._read_text(path)
        if text is None:
            return None
        try:
            parsed = parse_document(text)
        except FrontmatterError:
            return None
        if not parsed.frontmatter:
            return None
        concept_id = _identity_of(parsed.frontmatter)
        if concept_id is None:
            return None
        return StoredDocument(
            concept_id=concept_id,
            path=path,
            frontmatter=parsed.frontmatter,
            body=parsed.body,
            body_offset=parsed.body_offset,
        )

    def _identity_at(self, path: VaultPath) -> ConceptId | None:
        document = self._read(path)
        return document.concept_id if document is not None else None

    def _ensure_scan(self) -> _Scan:
        if self._scan is None:
            self._scan = self._build_scan()
        return self._scan

    def _build_scan(self) -> _Scan:
        by_id: dict[ConceptId, VaultPath] = {}
        clashes: dict[ConceptId, list[VaultPath]] = {}
        problems: list[StoreProblem] = []

        for path in self._markdown_paths():
            text = self._read_text(path)
            if text is None:
                continue
            try:
                parsed = parse_document(text)
            except FrontmatterError as error:
                problems.append(StoreProblem(path, "unreadable_frontmatter", str(error)))
                continue
            if not parsed.frontmatter:
                # Plain Markdown is legitimate vault content, not a problem --
                # unless the file *tried* to have frontmatter and failed. An
                # opening delimiter that never closes on a line of its own
                # (`---# Heading`, say) parses as no frontmatter at all, so
                # without this check the file would look like prose and be
                # skipped in silence by `index`, `validate` and `doctor` alike.
                if _opens_a_frontmatter_block(text):
                    problems.append(
                        StoreProblem(
                            path,
                            "unterminated_frontmatter",
                            "the frontmatter block opens with `---` and never closes on a "
                            "line of its own",
                        )
                    )
                continue

            raw = parsed.frontmatter.get("id")
            if raw is None:
                problems.append(StoreProblem(path, "missing_id", "frontmatter declares no id"))
                continue
            concept_id = _identity_of(parsed.frontmatter)
            if concept_id is None:
                problems.append(
                    StoreProblem(path, "invalid_id", f"{raw!r} is not a canonical UUIDv7")
                )
                continue

            if concept_id in clashes:
                clashes[concept_id].append(path)
            elif concept_id in by_id:
                clashes[concept_id] = [by_id[concept_id], path]
            else:
                by_id[concept_id] = path

        return _Scan(
            by_id=by_id,
            duplicates={key: tuple(value) for key, value in clashes.items()},
            problems=tuple(problems),
        )

    def _write(self, document: StoredDocument) -> None:
        absolute = self._absolute(document.path)
        absolute.parent.mkdir(parents=True, exist_ok=True)

        existing = self._read_text(document.path)
        frontmatter: Mapping[str, Any] = document.frontmatter
        line_ending = "\n"
        if existing is not None:
            try:
                parsed = parse_document(existing)
            except FrontmatterError:
                parsed = None
            if parsed is not None:
                line_ending = parsed.line_ending
                if parsed.frontmatter is not None:
                    frontmatter = _merge(parsed.frontmatter, document.frontmatter)

        rendered = render_document(frontmatter, document.body, line_ending=line_ending)
        if existing == rendered:
            # Nothing changed. Do not touch the file, do not touch its mtime.
            return
        _write_atomically(absolute, rendered)


def _identity_of(frontmatter: Mapping[str, Any]) -> ConceptId | None:
    raw = frontmatter.get("id")
    if not isinstance(raw, str):
        return None
    try:
        return ConceptId.parse(raw)
    except Exception:
        return None


def _merge(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> Mapping[str, Any]:
    """Apply ``incoming`` onto the mapping already on disk.

    Mutating the parsed mapping rather than rebuilding it is what keeps its
    comments, key order and quoting style. Keys the caller dropped are removed;
    new keys are appended.
    """
    if not isinstance(existing, MutableMapping):
        return dict(incoming)
    merged: MutableMapping[str, Any] = existing
    for key in [key for key in merged if key not in incoming]:
        del merged[key]
    for key, value in incoming.items():
        merged[key] = value
    return merged


def _write_atomically(target: Path, text: str) -> None:
    """Write via a temporary file in the same directory, then rename.

    A half-written canonical document is worse than no write at all, and the
    user's editor may be watching the file.
    """
    handle, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding=_ENCODING, newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _opens_a_frontmatter_block(text: str) -> bool:
    """Whether the file's first line is a frontmatter delimiter.

    Deliberately the narrowest test that separates "meant to be a concept" from
    "is prose". Plain Markdown does not begin with `---`: a horizontal rule
    needs something above it, and a setext heading underline needs `===` or a
    line of text before it. So an opening delimiter is intent, and a file that
    declares intent and then cannot be read is worth naming.

    Never a repair. core/02 section 23 keeps repair explicit, and the right fix
    is not inferable -- the file may be a concept missing one newline, or
    Markdown that genuinely opens with a rule. Never4gA says which file and why.
    """
    first, _, _ = text.partition("\n")
    return first.strip() == "---"
