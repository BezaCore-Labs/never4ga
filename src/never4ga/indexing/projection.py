"""One canonical document, as the indexes need to see it.

details/data-indexing-maintenance.md section 12 sets the shape of the pipeline
and the rule for bad input: "Invalid concept documents are reported rather than
silently skipped. Where safe, useful text may still be indexed with a validation
warning." So this function is total. It never refuses a document; it returns
what it could build and a list of what was wrong, and the indexing service
decides what to do about the issues.

That matters more than it looks. A vault is hand-edited in Obsidian by someone
who is not thinking about the index. A projection that raised on the first
malformed relation would make one typo cost the searchability of the note that
contains it -- and of the run that was indexing it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from never4ga.domain.document import ForeignNote, StoredDocument, VaultPath, written_title
from never4ga.domain.identity import ConceptId
from never4ga.errors import IdentityError, VaultPathError
from never4ga.indexing.chunks import build_chunks, build_foreign_chunks
from never4ga.indexing.markdown import scan_links
from never4ga.ports.graph_index import GraphEdge
from never4ga.ports.metadata_index import MetadataRecord
from never4ga.ports.text_index import FIELD_KEYWORDS, FIELD_TITLE, IndexedChunk
from never4ga.schema.types import AREA_SCOPE_FIELD

__all__ = [
    "DocumentLink",
    "DocumentProjection",
    "ProjectionIssue",
    "document_names",
    "project",
    "project_foreign",
    "record_names",
    "written_title",
]

#: Frontmatter that becomes a column of the metadata projection. Everything else
#: is carried whole in ``extra`` so nothing is lost (core/00 #15).
_MODELLED: Final = frozenset(
    {"id", "type", "title", "tags", "domains", "lifecycle", "status", "authority", "workspace"}
)

#: A stand-in source for a link that resolves from the vault root rather than
#: from the document containing it. `_resolve` strips the final segment as the
#: containing directory, so a one-segment path leaves the root itself.
_VAULT_ROOT: Final = VaultPath(("index.md",))

#: Fields whose text is worth finding a document by, beyond its prose
#: (details/data-indexing-maintenance.md section 7).
_KEYWORD_FIELDS: Final = ("description", "aliases", "tags", "domains")


@dataclass(frozen=True, slots=True)
class ProjectionIssue:
    """Something wrong with a document, reported rather than raised."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DocumentLink:
    """One link, resolved as far as this document allows.

    Resolving a *path* is local work: a Markdown link is relative to the file it
    sits in, and `[[folder/Note]]` is Obsidian's absolute-in-vault form. Both
    produce ``target_path`` here.

    A bare ``[[Note]]`` is a *name*, which Obsidian resolves against the whole
    vault. That needs every document in view, so ``target_name`` travels to the
    indexing service instead. Exactly one of the two is set.
    """

    source: ConceptId
    raw_target: str
    target_path: VaultPath | None
    anchor: str | None
    is_external: bool
    target_name: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentProjection:
    """Everything one document contributes to the derived indexes."""

    record: MetadataRecord
    content_hash: str
    chunks: tuple[IndexedChunk, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    links: tuple[DocumentLink, ...] = ()
    issues: tuple[ProjectionIssue, ...] = field(default_factory=tuple)


def project(document: StoredDocument) -> DocumentProjection:
    """Project one document. Always succeeds; problems come back as issues."""
    issues: list[ProjectionIssue] = []
    frontmatter = document.frontmatter

    return DocumentProjection(
        record=_record(document, issues),
        content_hash=document.content_hash,
        chunks=_chunks(document),
        edges=tuple(_edges(document, frontmatter.get("relations"), issues)),
        links=tuple(_links(document, issues)),
        issues=tuple(issues),
    )


def document_names(document: StoredDocument) -> tuple[str, ...]:
    """Every name a bare ``[[wikilink]]`` may address this document by.

    Obsidian matches a filename stem and the document's ``aliases``. It does not
    match the title, and neither does this: a link that fails in the editor
    should fail here, because a resolver that disagrees with what the user sees
    is worse than one that finds less.

    Each name appears once. An alias repeating the stem, or two aliases
    differing only in case, is one name however many times it is written --
    see :func:`_distinct`.
    """
    stem = document.path.name.removesuffix(".md")
    return _distinct(stem, _sequence(document.frontmatter.get("aliases")))


def record_names(record: MetadataRecord) -> tuple[str, ...]:
    """:func:`document_names`, answered from the projection instead of the file.

    ``aliases`` is not a modelled column, so it rides in ``extra`` verbatim --
    which means everything :func:`document_names` reads survives projection,
    and a settled reconcile pass can resolve a changed document's wikilinks
    against neighbours it never had to parse.
    `tests/unit/test_projection_names.py` holds the two functions to the same
    answer; a name one produces and the other does not is a resolver that
    disagrees with the editor, which is the exact failure
    :func:`document_names` exists to rule out.
    """
    stem = record.path.name.removesuffix(".md")
    return _distinct(stem, _sequence(record.extra.get("aliases")))


def _distinct(stem: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    """The stem and its aliases, each name kept once, in the order written.

    Names are matched case-insensitively, so the same name spelled two ways is
    one name and the first spelling is the one kept.

    This is not tidiness. `_names` builds its map by appending one entry per
    name a document returns, and `_by_name` treats a name reaching more than
    one entry as ambiguous and resolves it to nothing. A document that names
    itself twice would therefore be unreachable by any link -- and would be
    reported as `ambiguous_link: matches 2 documents` naming one document.
    Writing `aliases: [ollama]` on `ollama.md` is the obvious thing to do, so
    the case is common.

    The ambiguity rule is separate: two documents answering to one name still
    resolve to nothing on purpose, because picking a winner is a heuristic.
    """
    names: list[str] = []
    seen: set[str] = set()
    for name in (stem, *aliases):
        folded = name.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        names.append(name)
    return tuple(names)


def _record(document: StoredDocument, issues: list[ProjectionIssue]) -> MetadataRecord:
    frontmatter = document.frontmatter
    title = frontmatter.get("title")
    if not isinstance(title, str) or not title.strip():
        # The filename is a poor title but a true one, and a note with no title
        # is still worth finding. `validate` reports the underlying problem.
        title = document.path.name.removesuffix(".md")
        issues.append(
            ProjectionIssue("missing_title", f"{document.path} has no title; using the filename")
        )

    return MetadataRecord(
        concept_id=document.concept_id,
        concept_type=_string(frontmatter.get("type")) or "unknown",
        path=document.path,
        title=title,
        workspace_id=_scope_owner(frontmatter, document, issues),
        tags=_sequence(frontmatter.get("tags")),
        domains=_sequence(frontmatter.get("domains")),
        lifecycle=_string(frontmatter.get("lifecycle")),
        status=_string(frontmatter.get("status")),
        authority=_string(frontmatter.get("authority")),
        extra={key: value for key, value in frontmatter.items() if key not in _MODELLED},
    )


def _chunks(document: StoredDocument) -> tuple[IndexedChunk, ...]:
    """Chunks, each carrying the document-level fields the lexical index weights."""
    metadata = {
        FIELD_TITLE: _string(document.frontmatter.get("title")) or "",
        FIELD_KEYWORDS: _keywords(document.frontmatter),
    }
    return tuple(
        IndexedChunk(
            chunk=chunk.chunk,
            path=chunk.path,
            text=chunk.text,
            line_range=chunk.line_range,
            metadata=metadata,
        )
        for chunk in build_chunks(document)
    )


def project_foreign(note: ForeignNote) -> tuple[IndexedChunk, ...]:
    """A foreign note's chunks, and nothing else (core/01 section 1).

    No metadata record, no edges, no links: a foreign note is indexed for
    search only. Its chunks carry the lexical fields a concept's do, read from
    what the writer wrote -- :attr:`ForeignNote.title` -- so the index weights
    the pile the way it weights the vault.
    """
    metadata = {
        FIELD_TITLE: note.title,
        FIELD_KEYWORDS: _keywords(note.frontmatter),
    }
    return tuple(
        IndexedChunk(
            chunk=chunk.chunk,
            path=chunk.path,
            text=chunk.text,
            line_range=chunk.line_range,
            metadata=metadata,
        )
        for chunk in build_foreign_chunks(note)
    )


def _keywords(frontmatter: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for name in _KEYWORD_FIELDS:
        value = frontmatter.get(name)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Sequence):
            parts.extend(str(item) for item in value)
    return " ".join(part for part in parts if part)


def _edges(
    document: StoredDocument, relations: Any, issues: list[ProjectionIssue]
) -> list[GraphEdge]:
    if relations is None:
        return []
    if isinstance(relations, str) or not isinstance(relations, Sequence):
        issues.append(
            ProjectionIssue(
                "invalid_relations",
                f"{document.path}: `relations` must be a list of typed relations",
            )
        )
        return []

    edges: list[GraphEdge] = []
    for relation in relations:
        if not isinstance(relation, Mapping) or not _string(relation.get("type")):
            issues.append(
                ProjectionIssue(
                    "invalid_relation",
                    f"{document.path}: a relation needs a `type` and a `target` "
                    "(core/02 section 17.1)",
                )
            )
            continue
        target = _reference(
            relation.get("target"), document, issues, code="invalid_relation_target"
        )
        if target is None:
            continue
        # An unregistered type is kept as written: core/02 section 17.3 requires
        # unknown relation types to survive.
        edges.append(
            GraphEdge(
                source=document.concept_id,
                target=target,
                relation_type=str(relation["type"]).strip(),
                attributes={
                    key: value for key, value in relation.items() if key not in ("type", "target")
                },
            )
        )
    return edges


def _links(document: StoredDocument, issues: list[ProjectionIssue]) -> list[DocumentLink]:
    links: list[DocumentLink] = []
    for link in scan_links(document.body):
        target_path: VaultPath | None = None
        target_name: str | None = None
        if link.is_external:
            pass
        elif not link.target:
            # A bare anchor addresses this document.
            target_path = document.path
        elif link.is_wikilink and "/" not in link.target:
            target_name = link.target
        elif link.is_wikilink:
            # Obsidian's absolute-in-vault form, and it omits the extension.
            target_path = _resolve(_VAULT_ROOT, f"{link.target}.md", issues)
        elif not link.target.endswith("/"):
            target_path = _resolve(document.path, link.target, issues)
        links.append(
            DocumentLink(
                source=document.concept_id,
                raw_target=link.target,
                target_path=target_path,
                anchor=link.anchor,
                is_external=link.is_external,
                target_name=target_name,
            )
        )
    return links


def _resolve(source: VaultPath, target: str, issues: list[ProjectionIssue]) -> VaultPath | None:
    """Resolve a link against the directory holding the source document.

    Markdown links in the vault are written the way a reader types them, which
    is relative to the file they are in, as the scaffolded `index.md` files
    are.
    """
    segments = list(source.segments[:-1])
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not segments:
                issues.append(
                    ProjectionIssue(
                        "link_escapes_vault", f"{source}: link {target!r} points outside the vault"
                    )
                )
                return None
            segments.pop()
            continue
        segments.append(part)
    try:
        return VaultPath(tuple(segments)) if segments else None
    except VaultPathError:
        return None


def _scope_owner(
    frontmatter: Mapping[str, Any], document: StoredDocument, issues: list[ProjectionIssue]
) -> ConceptId | None:
    """The workspace a document belongs to, or failing that its life area.

    `area` names a life-area scope (core/02 section 16.3), and an area holds
    its own documents (core/01 section 7). Reading `workspace` alone would
    leave every decision, plan and log under `20_Life/<Area>/` with no scope
    owner in the index. A workspace inside an area carries the area's id in
    its parent chain, and nothing in the index would answer to it.

    A document naming both belongs to the workspace. The area is where it
    sits; the workspace is what it is about.
    """
    workspace = _reference(frontmatter.get("workspace"), document, issues)
    if workspace is not None:
        return workspace
    return _reference(
        frontmatter.get(AREA_SCOPE_FIELD), document, issues, code="invalid_area_reference"
    )


def _reference(
    value: Any,
    document: StoredDocument,
    issues: list[ProjectionIssue],
    *,
    code: str = "invalid_workspace_reference",
) -> ConceptId | None:
    """A frontmatter field that should hold a Never4gA identity."""
    if value is None:
        return None
    try:
        return ConceptId.parse(value if isinstance(value, str) else str(value))
    except IdentityError:
        issues.append(
            ProjectionIssue(code, f"{document.path}: {value!r} is not a canonical Never4gA id")
        )
        return None


def _string(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _sequence(value: Any) -> tuple[str, ...]:
    """A frontmatter list, tolerating the scalar a hand-edited vault will contain."""
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()
