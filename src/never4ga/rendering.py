"""Domain objects as the one payload shape every interface returns.

The same request produces byte-identical structured output through the CLI or
through MCP, because both render one service result through one renderer. A
parity test that only compared meaning could not catch two renderers drifting
apart, and drifting renderers would quietly break `core/05` section 13's three
interfaces over the same services.

A composition root may not import another, so the shape lives here, where every
root may see it and none owns it.

A leaf: it takes domain and service values and returns dictionaries. It reads
nothing, opens nothing and decides nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from never4ga.context.budget import REQUIRED_READING_CEILING
from never4ga.domain.context import ContextPack
from never4ga.domain.document import VaultPath
from never4ga.domain.provenance import AcquisitionReason
from never4ga.domain.sessions import Checkpoint
from never4ga.schema import (
    REQUIRED_BASE_FIELDS,
    SCHEMA_VERSION,
    TYPE_REGISTRY,
    Location,
    LocationKind,
    TypeSpec,
    field_kind,
)
from never4ga.services.authoring import format_timestamp
from never4ga.services.capture import Captured
from never4ga.services.creation import Created
from never4ga.services.doctor import Diagnosis
from never4ga.services.inbox import Resolved, ScratchLine
from never4ga.services.indexing import IndexRun
from never4ga.services.search import ConceptView, SearchResponse
from never4ga.services.workspaces import ScopeResolution
from never4ga.services.wrap import CONTEXT_QUESTION, Wrapped

__all__ = [
    "captured",
    "checkpoint",
    "concept_view",
    "created",
    "diagnosis",
    "index_run",
    "reason",
    "resolved_scope",
    "scope_pack",
    "search_response",
    "wrapped",
]


def reason(value: AcquisitionReason) -> dict[str, str]:
    """Why something is in a pack, which every item carries (`core/07`)."""
    return {"stage": str(value.stage), "code": str(value.code), "detail": value.detail}


def index_run(run: IndexRun) -> dict[str, Any]:
    return {
        "indexed": run.indexed,
        "unchanged": run.unchanged,
        "removed": run.removed,
        "issues": [{"code": issue.code, "message": issue.message} for issue in run.issues],
        # Foreign notes held by path (`core/02` section 3.3), counted apart
        # from concepts. Always present, zero in a vault with no foreign
        # material: a key that appears only sometimes is a second payload shape.
        "foreign": {
            "indexed": run.foreign_indexed,
            "unchanged": run.foreign_unchanged,
            "removed": run.foreign_removed,
        },
    }


def search_response(response: SearchResponse, *, explain: bool = False) -> dict[str, Any]:
    """One payload, three surfaces.

    ``explain`` adds the fused score and the rank each lane gave a result.
    **The explain payload is explicitly unstable**: it exists so that claims
    about retrieval quality can be checked, and it will change while the
    ranking is tuned. Nothing should parse it and expect to keep working.
    """
    return {
        "index_is_stale": response.index_is_stale,
        "results": [
            {
                # Null for a foreign note, as `type` is: always present, so
                # there is one shape.
                "id": None if result.concept_id is None else str(result.concept_id),
                "path": str(result.path),
                "title": result.title,
                "type": result.concept_type,
                "rank": result.rank,
                "retriever": result.retriever,
                "reason": result.reason.code,
                "heading_path": list(result.heading_path),
                "excerpt": result.excerpt,
                "lines": (
                    [result.line_range.start, result.line_range.end] if result.line_range else None
                ),
                "authority": result.authority,
                "status": result.status,
                "lifecycle": result.lifecycle,
                "is_stale": result.is_stale,
                "chunk_has_changed": result.chunk_has_changed,
                # Always present, `None` when it was not asked for. A key that
                # appears only sometimes is a second payload shape.
                "explain": (
                    {"score": result.score, "lanes": dict(result.lanes)} if explain else None
                ),
            }
            for result in response.results
        ],
    }


def scope_pack(depth: str, pack: ContextPack, resolution: ScopeResolution) -> dict[str, Any]:
    """A Context Pack and the scope it was assembled for."""
    scope = resolution.scope
    return {
        "depth": depth,
        "scope": {
            "workspace_id": str(scope.workspace_id),
            "workspace_path": str(scope.workspace_path),
            "repository_root": (
                str(scope.repository_root) if scope.repository_root is not None else None
            ),
            "parent_chain": [str(parent) for parent in scope.parent_chain],
            "reason": reason(scope.reason),
            "conflicts": list(resolution.conflicts),
        },
        "items": [
            {
                "id": None if item.concept_id is None else str(item.concept_id),
                "path": str(item.path),
                "title": item.title,
                "category": str(item.category),
                "priority": item.priority,
                "reason": reason(item.reason),
                "is_reference": item.is_reference,
                "reference_reason": (
                    None if item.reference_reason is None else str(item.reference_reason)
                ),
                "required": item.required,
                "size": item.size,
                "description": item.description,
                # Filled by a client that knows where the vault is on disk
                # (`with_disk_paths`); the service answering it may not.
                "disk_path": None,
                "is_stale": item.is_stale,
                "stale_since": (
                    None if item.stale_since is None else format_timestamp(item.stale_since)
                ),
                "body": item.body,
            }
            for item in pack.items
        ],
        "signals": [
            {
                "provider": signal.provider_id,
                "kind": signal.kind,
                "value": signal.value,
                "reason": reason(signal.reason),
            }
            for signal in pack.signals
        ],
        "usage": {
            "items": pack.usage.items,
            "characters": pack.usage.characters,
            "estimated_tokens": pack.usage.estimated_tokens,
            "full_documents": pack.usage.full_documents,
            "dropped_items": pack.usage.dropped_items,
            "required_characters": pack.usage.required_characters,
            "required_ceiling": REQUIRED_READING_CEILING,
        },
        "degraded_providers": list(pack.degraded_providers),
        "llm_stages": list(pack.llm_stages),
        "client": pack.client,
        "task_given": pack.task_given,
        # Always present, null when a focused or deep retrieval opened no
        # session, the same way `client` is always present. An absent key and
        # a null key are different shapes, and the API sends null.
        "session_id": None if pack.session is None else str(pack.session),
    }


def resolved_scope(resolution: ScopeResolution) -> dict[str, Any]:
    """Which workspace a directory belongs to, and why.

    A flatter shape than :func:`scope_pack`'s ``scope`` member and deliberately
    so: this answers a question a person asked, and that one describes a pack.
    """
    scope = resolution.scope
    return {
        "workspace": str(scope.workspace_id),
        "workspace_path": str(scope.workspace_path),
        "repository_root": str(scope.repository_root) if scope.repository_root else None,
        "parent_chain": [str(parent) for parent in scope.parent_chain],
        "reason": {"code": str(scope.reason.code), "detail": scope.reason.detail},
        "conflicts": list(resolution.conflicts),
    }


def diagnosis(root: str, found: Diagnosis) -> dict[str, Any]:
    return {
        "vault": root,
        "vault_id": str(found.vault_id) if found.vault_id else None,
        "healthy": found.healthy,
        "concept_count": found.concept_count,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity.value,
                "message": finding.message,
                "path": str(finding.path) if finding.path else None,
                "repair_hint": finding.repair_hint,
            }
            for finding in found.findings
        ],
    }


def concept_view(view: ConceptView) -> dict[str, Any]:
    """One concept, with what the projection knows about its relations."""
    return {
        "id": str(view.document.concept_id),
        "path": str(view.document.path),
        "frontmatter": dict(view.document.frontmatter),
        "body": view.document.body,
        "index_is_stale": view.index_is_stale,
        "relations": {
            "outgoing": [
                {"id": str(one.concept_id), "type": one.relation_type} for one in view.outgoing
            ],
            "incoming": [
                {"id": str(one.concept_id), "type": one.relation_type} for one in view.incoming
            ],
        },
    }


def created(new: Created) -> dict[str, Any]:
    """A concept that was just written, and what had to be made for it.

    ``placement`` is present only when something chose a folder on the caller's
    behalf, such as an activity_log's year or a type's own location, because a
    reason that says nothing is worse than no reason.
    """
    payload: dict[str, Any] = {
        "id": str(new.concept_id),
        "path": str(new.path),
        "created_directories": [str(path) for path in new.directories],
        "created_files": [str(path) for path in new.files],
    }
    if new.placement_reason:
        payload["placement"] = new.placement_reason
    if new.plan_link:
        # A started walkthrough's link, for the session to add under its phase
        # in the hand-written plan.
        payload["plan_link"] = new.plan_link
    if new.moved_from is not None:
        # Adoption out of foreign material is the one write that relocates a
        # file, and a caller holding the old path needs to know it no longer
        # names anything.
        payload["moved_from"] = str(new.moved_from)
    if new.set_aside:
        # What the writer wrote that the concept does not carry, kept under
        # `extensions.adopted` rather than refused (`core/02` section 3.3).
        payload["set_aside"] = list(new.set_aside)
    return payload


def checkpoint(recorded: Checkpoint) -> dict[str, Any]:
    """What a session recorded mid-flight. Runtime state, never the vault."""
    return {
        "session_id": str(recorded.session),
        "recorded_at": recorded.recorded_at.isoformat(),
        "note": recorded.note,
        "actions": list(recorded.actions),
        "decisions": list(recorded.decisions),
        "memories": list(recorded.memories),
        "work": list(recorded.work),
        "context": list(recorded.context),
    }


def wrapped(session: Wrapped) -> dict[str, Any]:
    """The one activity_log a session leaves, and what it declared on the way.

    ``decisions``, ``memories`` and ``work`` are handed back rather than acted
    on. The agent declares and Never4gA records; turning one into a decision
    record is a step a person takes, and so is acting on a tracker candidate.
    """
    return {
        "session_id": str(session.session),
        "id": str(session.concept_id),
        "path": str(session.document.path),
        # Where it was, when a retitle moved it. Always present so a
        # client can read the absence of a move rather than infer it from a
        # missing key.
        "renamed_from": str(session.renamed_from) if session.renamed_from else None,
        # Where the session opened, when the log was directed elsewhere because
        # that workspace is gone. Same shape as `renamed_from`.
        "redirected_from": str(session.redirected_from) if session.redirected_from else None,
        "checkpoints": session.checkpoints,
        "updated": session.updated,
        "decisions": list(session.decisions),
        "memories": list(session.memories),
        "work": list(session.work),
        # The gap between what was declared and what Never4gA saw done about
        # it. Part of the payload rather than of the CLI's prose, so the API
        # and MCP can hold a session to the same account.
        "outstanding_work": list(session.outstanding_work),
        # What the session's startup carried from `Context/`, and the question
        # every wrap asks about it (`core/04`, "Workspace state changes").
        # Present whether or not anything was declared, because the session
        # that forgets to declare is the one it is for.
        "context": list(session.context),
        "context_given": [
            {"id": str(given.concept_id), "path": str(given.path), "title": given.title}
            for given in session.context_given
        ],
        "context_question": CONTEXT_QUESTION if session.context_given else None,
        "outstanding_context": list(session.outstanding_context),
    }


def inbox_pending(paths: Sequence[VaultPath]) -> dict[str, Any]:
    """What is waiting, counted and listed. The count is never truncated."""
    return {"pending": len(paths), "items": [str(path) for path in paths]}


def inbox_resolved(resolved: Resolved) -> dict[str, Any]:
    """One item, cleared, and what accounted for it."""
    return {
        "path": str(resolved.path),
        "became": resolved.became,
        "reason": resolved.reason,
        "discarded": resolved.became is None,
    }


def scratch_lines(lines: Sequence[ScratchLine]) -> dict[str, Any]:
    """The scratchpad, numbered as a reader sees it."""
    return {
        "lines": len(lines),
        "items": [{"number": line.number, "text": line.text} for line in lines],
    }


def scratch_written(line: ScratchLine) -> dict[str, Any]:
    """One thought, and where it landed."""
    return {"number": line.number, "text": line.text}


def captured(note: Captured) -> dict[str, Any]:
    """An Inbox note. Two fields, because capture decides nothing else."""
    return {"path": str(note.path), "title": note.title}


def type_registry() -> dict[str, Any]:
    """The Type Registry as every interface publishes it.

    The registered types, what each requires, the optional fields it carries
    and what kind each one is, and its lifecycle vocabulary. A surface with
    this can offer a dropdown of real types, render `unit` and `due` as
    inputs, and constrain a lifecycle to its legal values instead of offering
    a free-text box.

    Templates are body-only, so they do not advertise optional fields. This
    does, where the person is working rather than in a file they may never
    open.

    A leaf like everything here: it reads the registry and returns
    dictionaries. The registry is a property of the schema version, so this
    takes no vault and no session.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "required_base_fields": list(REQUIRED_BASE_FIELDS),
        "types": [_type_spec(spec) for _, spec in sorted(TYPE_REGISTRY.items())],
    }


def _type_spec(spec: TypeSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "locations": [_location(location) for location in spec.locations],
        "location_is_binding": spec.location_is_binding,
        "required_fields": list(spec.required_fields),
        "lifecycle_values": list(spec.lifecycle_values),
        "optional_fields": [{"name": name, "kind": field_kind(name)} for name in spec.extra_fields],
        "recommended_authority": spec.recommended_authority,
        "creatable": spec.creatable,
    }


def _location(location: Location) -> str:
    """One place a type may live, said the way a person would say it.

    Words rather than an enum plus a value: a client renders this to explain
    where a document will land, and "a workspace's Units/" is the sentence it
    would have had to assemble anyway.
    """
    match location.kind:
        case LocationKind.WORKSPACE_SECTION:
            return f"a workspace's {location.value}/"
        case LocationKind.WORKSPACE_MANIFEST:
            return "a workspace's own workspace.md"
        case LocationKind.LIFE_AREA_MANIFEST:
            return "a life area's own area.md"
        case LocationKind.LIFE_AREA_CONTENT:
            return "inside a life area under 20_Life/"
        case LocationKind.EXACT_PATH:
            return location.value
        case _:
            return f"{location.value}/"


def with_disk_paths(payload: Mapping[str, Any], vault_root: Path) -> dict[str, Any]:
    """A pack with each item's absolute path, from the client's own vault root.

    Required reading that does not fit inline is read from its file (`core/07`
    section 10), so an agent needs a path its file reader can open. The HTTP
    service does not know where the vault sits on disk for the caller, and
    does not need to; the CLI and MCP clients do, so they add it, whether the
    pack was assembled in process or answered by the service.
    """
    return {
        **payload,
        "items": [
            {**item, "disk_path": str(vault_root / item["path"])} for item in payload["items"]
        ],
    }
