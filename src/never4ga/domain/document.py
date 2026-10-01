"""Canonical document model.

The shape of a canonical document as it crosses a port boundary. This module
deliberately does *not* parse Markdown or validate Schema v0.1. Frontmatter is
carried as an opaque read-only mapping so unknown extension fields survive a
round trip unchanged (core/00 #15).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from never4ga.domain.identity import ConceptId
from never4ga.errors import VaultPathError

__all__ = ["ForeignNote", "StoredDocument", "UntrackedMarkdown", "VaultPath", "written_title"]


@dataclass(frozen=True, slots=True, order=True)
class VaultPath:
    """A vault-relative POSIX path.

    Paths are locations, not identity (core/02 section 5.1). Absolute paths and
    parent-escaping segments are rejected so that a path from an untrusted
    source cannot address anything outside the vault.
    """

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        # Refused here as well as in `parse`: slicing the parent of a top-level
        # file would otherwise produce an empty path that no layout rule
        # expects.
        if not self.segments:
            raise VaultPathError("a vault path has at least one segment")

    @classmethod
    def parse(cls, raw: str) -> VaultPath:
        if not raw.strip():
            raise VaultPathError("vault paths must be non-empty strings")
        if raw.startswith("/") or raw.startswith("\\"):
            raise VaultPathError(f"{raw!r} is absolute; vault paths are vault-relative")
        parts: list[str] = []
        for segment in raw.replace("\\", "/").split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                raise VaultPathError(f"{raw!r} escapes the vault root")
            parts.append(segment)
        if not parts:
            raise VaultPathError(f"{raw!r} resolves to no path segments")
        return cls(tuple(parts))

    @property
    def root(self) -> str:
        """The stable vault root this path sits under (core/01)."""
        return self.segments[0]

    @property
    def name(self) -> str:
        return self.segments[-1]

    def __str__(self) -> str:
        return "/".join(self.segments)


def _stable_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class UntrackedMarkdown:
    """Markdown in the vault that is not (yet) a concept.

    What adoption starts from: a file a person wrote by hand, carrying no
    ``id`` -- either no frontmatter at all, or a partial block whose fields
    must survive adoption unchanged (core/02 section 5.1).
    """

    path: VaultPath
    #: Empty when the file opens no frontmatter block.
    frontmatter: Mapping[str, Any]
    body: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "frontmatter", MappingProxyType(dict(self.frontmatter)))


@dataclass(frozen=True, slots=True)
class ForeignNote:
    """A note inside registered foreign material that is not a concept.

    The writer's file as it is: frontmatter of their own or none, and a body.
    It has no identity, and nothing here gives it one -- the indexer holds it
    by ``path``, which is derived and rebuildable and never a concept identity
    (core/01 section 1). A note whose frontmatter cannot be read is carried
    whole as its body, with no frontmatter: the bytes are still worth finding,
    and `doctor` is what reports the rest.
    """

    path: VaultPath
    frontmatter: Mapping[str, Any]
    body: str
    #: As :attr:`StoredDocument.body_offset`: the lines before ``body``, so a
    #: chunk reports lines a person opening the file would count.
    body_offset: int = field(default=0, compare=False)
    _content_hash: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "frontmatter", MappingProxyType(dict(self.frontmatter)))
        # Path-independent, as a concept's is: a moved note hashes the same,
        # and it is the path record, not the hash, that notices the move.
        serialised = json.dumps(dict(self.frontmatter), sort_keys=True, default=repr)
        object.__setattr__(self, "_content_hash", _stable_hash(serialised, self.body))

    @property
    def content_hash(self) -> str:
        return self._content_hash

    @property
    def title(self) -> str:
        """What the note is called: a ``title`` it declares, else its written title.

        One rule for the index and for every surface that shows the note, so
        a result and the chunk it matched never disagree about the name.
        """
        declared = self.frontmatter.get("title")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
        return written_title(self.path, self.body)


def written_title(path: VaultPath, body: str) -> str:
    """The title a hand-written file already has.

    The first heading is what the writer called it; failing that, the filename
    is the only name the document has ever had, read back with hyphens as
    spaces (hyphens join the words of one idea). Nothing is invented: both are
    the writer's own words. `adopt` titles a concept by it, and the indexer
    titles a foreign note by it, so the two agree before and after.
    """
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.name.removesuffix(".md").replace("-", " ")


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """A canonical Markdown document as seen through :class:`DocumentStore`."""

    concept_id: ConceptId
    path: VaultPath
    frontmatter: Mapping[str, Any]
    body: str
    #: How many lines of the file precede ``body``. Frontmatter is not part of
    #: the body, but a reader opening the file counts from line 1, so anything
    #: reporting source lines adds this. It is a fact about how the document was
    #: rendered rather than about the concept, which is why it takes no part in
    #: identity, equality or the content hash: the same concept written with a
    #: longer frontmatter block is still the same concept.
    body_offset: int = field(default=0, compare=False)
    _content_hash: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "frontmatter", MappingProxyType(dict(self.frontmatter)))
        # Content hash covers canonical content only: identity and content are
        # path-independent, so a move must not change the hash.
        serialised = json.dumps(dict(self.frontmatter), sort_keys=True, default=repr)
        object.__setattr__(self, "_content_hash", _stable_hash(serialised, self.body))

    @property
    def content_hash(self) -> str:
        return self._content_hash
