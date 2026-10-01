"""Stable chunk identity.

core/06 section 4 requires an identity below the document level that is
deterministic from stable source material, so that Never4gA can always answer:

    This vector came from document X, heading Y, lines A-B, chunk-policy Z,
    content hash H, embedding model M.

The five identity inputs are fixed by the specification: document UUID,
normalised structural locator, chunk ordinal, content hash and chunking-policy
version. The embedding model belongs to the embedding namespace, not to chunk
identity -- re-embedding with a new model must not change chunk identity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import ConceptId

__all__ = ["ChunkIdentity", "LineRange"]


@dataclass(frozen=True, slots=True, order=True)
class LineRange:
    """A 1-based inclusive line span within the source document."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1:
            raise ValueError(f"line range start must be 1-based and positive, got {self.start}")
        if self.end < self.start:
            raise ValueError(f"line range end {self.end} precedes start {self.start}")


@dataclass(frozen=True, slots=True)
class ChunkIdentity:
    """Deterministic identity of a chunk of a canonical document.

    Or of a foreign note (core/01 section 1): a document `init` registered as
    foreign material has no `id`, so its chunks are owned by the note's
    vault-relative path instead. ``concept_id`` is then ``None`` and ``source``
    names the path. The path is derived and rebuildable and is never a concept
    identity -- a rename is a new document and the old one gone, which is what
    a path-keyed derived record should do.
    """

    concept_id: ConceptId | None
    heading_path: tuple[str, ...]
    ordinal: int
    content_hash: str
    policy_version: str
    #: The path that owns a chunk with no concept. Ignored -- and normally
    #: absent -- when ``concept_id`` is set: a concept's chunks are its own
    #: whatever path it sits at.
    source: VaultPath | None = None

    def __post_init__(self) -> None:
        if self.concept_id is None and self.source is None:
            raise ValueError("a chunk with no concept_id needs a source path to own it")
        if self.ordinal < 0:
            raise ValueError(f"chunk ordinal must be non-negative, got {self.ordinal}")
        if not self.content_hash.strip():
            raise ValueError("chunk content_hash is required")
        if not self.policy_version.strip():
            raise ValueError("chunk policy_version is required")

    @property
    def owner(self) -> str:
        """Who the chunk belongs to: the concept's id, or ``path:<path>``.

        The prefix keeps the two namespaces apart in a key: a UUID never
        starts with ``path:``, so no path can be mistaken for a concept.
        """
        if self.concept_id is not None:
            return str(self.concept_id)
        return f"path:{self.source}"

    @property
    def key(self) -> str:
        """A deterministic, backend-neutral chunk key.

        It is a hash of the five specified identity inputs, so it is stable
        across processes and machines and carries no backend-native ordinal.
        """
        digest = hashlib.sha256()
        for part in (
            self.owner,
            "\x1e".join(self.heading_path),
            str(self.ordinal),
            self.content_hash,
            self.policy_version,
        ):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x1f")
        return digest.hexdigest()
