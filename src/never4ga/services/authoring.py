"""Building canonical concepts.

One place decides what a freshly created Never4gA concept looks like, so that
the CLI, the HTTP API and the MCP server cannot drift apart
(core/05 section 15, details/api-cli-mcp-contract.md section 1).

Two rules from core/02 govern everything here. Timestamps are ISO 8601 with an
explicit offset and are written quoted (section 7.4). ``generated.at`` is never
fabricated (section 5.2): it is the moment this process actually wrote the
content, taken from the clock the caller supplied.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from never4ga.domain.document import StoredDocument, VaultPath
from never4ga.domain.identity import ConceptId
from never4ga.schema import SCHEMA_VERSION, type_spec

__all__ = [
    "NEVER4GA_ACTOR",
    "OWNER_ACTOR",
    "Clock",
    "build_concept",
    "format_timestamp",
    "utc_now",
]

#: core/02 section 14.1: the default human actor for a personal vault.
OWNER_ACTOR: Final = "human:owner"

#: Scaffolding Never4gA writes itself -- root index, templates, the Obsidian
#: pack -- is process output, not the user's prose and not model output.
NEVER4GA_ACTOR: Final = "process:never4ga"

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_timestamp(moment: datetime) -> str:
    """Render a timestamp the way core/02 section 7.4 asks for.

    UTC is spelled ``Z`` rather than ``+00:00``: it is what every example in the
    specification uses, and canonical writers SHOULD prefer UTC.
    """
    if moment.tzinfo is None:
        raise ValueError("a canonical timestamp requires an explicit offset")
    text = moment.astimezone(UTC).replace(microsecond=0).isoformat()
    return text.replace("+00:00", "Z")


def build_concept(
    *,
    concept_type: str,
    title: str,
    path: VaultPath,
    actor: str = OWNER_ACTOR,
    now: Clock = utc_now,
    concept_id: ConceptId | None = None,
    description: str | None = None,
    body: str = "",
    extra_fields: Mapping[str, Any] | None = None,
    **fields: Any,
) -> StoredDocument:
    """Assemble a canonical concept.

    Field order is deliberate and matches the "recommended durable concept" of
    core/02 section 36, because these files are read by humans in Obsidian: the
    five required fields first, then provenance, then everything else.

    ``authority`` defaults to whatever core/02 section 21 recommends for the
    type, and is omitted when the specification recommends nothing -- guessing
    would be asserting something about the content Never4gA cannot know.

    ``extra_fields`` carries frontmatter somebody else named -- the keys a
    person wrote by hand, the fields a caller supplied -- as a mapping rather
    than as keywords, because a key like ``path`` or ``body`` passed as a
    keyword collides with this function's own parameters.
    """
    timestamp = format_timestamp(now())
    frontmatter: dict[str, Any] = {
        "type": concept_type,
        "id": str(concept_id or ConceptId.new()),
        "schema": SCHEMA_VERSION,
        "title": title,
    }
    if description is not None:
        frontmatter["description"] = description
    frontmatter["created_at"] = timestamp
    frontmatter["generated"] = {"by": actor, "at": timestamp}

    spec = type_spec(concept_type)
    if spec is not None and spec.recommended_authority is not None:
        frontmatter["authority"] = spec.recommended_authority

    for key, value in {**fields, **(extra_fields or {})}.items():
        if value is not None:
            frontmatter[key] = value

    return StoredDocument(
        concept_id=ConceptId.parse(frontmatter["id"]),
        path=path,
        frontmatter=frontmatter,
        body=body,
    )


def with_updated_generation(
    document: StoredDocument,
    *,
    actor: str,
    now: Clock = utc_now,
    **changes: Any,
) -> StoredDocument:
    """Apply ``changes`` and record who changed the content, and when.

    core/02 section 5.2: agent/process-rewritten canonical content MUST include
    ``generated``, and ``generated.at`` is the last meaningful content change --
    not the file's mtime.
    """
    frontmatter: dict[str, Any] = dict(document.frontmatter)
    frontmatter.update(changes)
    frontmatter["generated"] = {"by": actor, "at": format_timestamp(now())}
    return StoredDocument(
        concept_id=document.concept_id,
        path=document.path,
        frontmatter=frontmatter,
        body=document.body,
    )


def frontmatter_of(document: StoredDocument) -> Mapping[str, Any]:
    return document.frontmatter
