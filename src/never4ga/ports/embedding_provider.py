"""EmbeddingProvider -- text to vectors.

core/06 section 10: the embedding provider MUST NOT be coupled to the vector
index. Any provider must be usable with any :class:`~never4ga.ports.vector_index.VectorIndex`.

Every embedding is stamped with its namespace (provider, model, dimension,
normalization, schema version). Changing model creates a *new* derived
namespace rather than mutating existing vectors.

**The interface is asymmetric.** A single ``embed(texts)`` would assume a model
that embeds a question the same way it embeds an answer. Modern retrieval models
do not: Qwen3, ``snowflake-arctic-embed`` and the E5 family want an ``Instruct:``
prefix on a query and nothing on a document, and omitting it costs roughly 1-5%
retrieval. A port that cannot express the difference cannot use any of them
properly.

The instruction exists only on the query side, so changing it never invalidates
a stored vector. That is why it is *not* part of :class:`EmbeddingNamespace`:
the namespace identifies a space that documents live in, and re-wording a query
prefix does not move them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["EmbeddingNamespace", "EmbeddingProvider", "EmbeddingVector"]

type EmbeddingVector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingNamespace:
    """Identifies a derived embedding space (core/06 section 10)."""

    provider: str
    model: str
    dimension: int
    normalization: str
    schema_version: str

    def __post_init__(self) -> None:
        if self.dimension < 1:
            raise ValueError(f"embedding dimension must be positive, got {self.dimension}")
        for name in ("provider", "model", "normalization", "schema_version"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"embedding namespace requires {name}")

    @property
    def name(self) -> str:
        """A stable, human-readable namespace key."""
        return (
            f"{self.provider}/{self.model}/d{self.dimension}"
            f"/{self.normalization}/{self.schema_version}"
        )


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def namespace(self) -> EmbeddingNamespace: ...

    @property
    def asymmetric(self) -> bool:
        """Whether a query is embedded differently from a document.

        Declared rather than discovered. A caller cannot tell by comparing
        vectors: a symmetric model returns the same one either way, and so does
        an asymmetric model whose instruction happens to be empty.
        """
        ...

    def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddingVector]:
        """Embed each document, preserving order and length."""
        ...

    def embed_query(self, text: str) -> EmbeddingVector:
        """Embed one search query, into the same space as the documents."""
        ...
