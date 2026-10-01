"""Schema v0.1 validation (core/02).

Validation reports; it never repairs. core/02 section 23 requires repair to be an
explicit act -- change the type, move the file, or register an extension -- and
section 31 requires a document to stay readable even when validation fails. So
every check here produces a structured issue and nothing here writes anything.

Issues follow details/api-cli-mcp-contract.md section 12: a stable machine code,
a human message, the field concerned, and a repair hint. Agents must not have to
parse English.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final

from never4ga.domain.document import VaultPath
from never4ga.domain.identity import CANONICAL_UUID_PATTERN, UUID_VERSION, ConceptId
from never4ga.layout import DocumentRole, flat_area_of, role_of
from never4ga.schema.types import AREA_SCOPE_FIELD, TypeSpec, type_spec
from never4ga.schema.vocabulary import (
    AUTHORITY_VALUES,
    BASE_OPTIONAL_FIELDS,
    BOOLEAN_FIELDS,
    DATE_FIELDS,
    DOMAIN_VALUES,
    KNOWN_PROFILES,
    PRIORITY_VALUES,
    RELATION_TYPES,
    REQUIRED_BASE_FIELDS,
    SCHEMA_VERSION,
    STATUS_VALUES,
    TIMESTAMP_FIELDS,
    WORKSPACE_TYPE_VALUES,
)

__all__ = [
    "Severity",
    "ValidationIssue",
    "ValidationLevel",
    "ValidationReport",
    "validate_document",
]

#: core/02 section 7.1: Never4gA-defined keys use lowercase snake_case.
_SNAKE_CASE: Final = re.compile(r"^[a-z][a-z0-9_]*$")

#: core/02 section 14.1 actor forms: `human:<id>`, `<producer>/<version>`,
#: `process:<id>`.
_ACTOR: Final = re.compile(r"^(?:human:[\w.-]+|process:[\w.-]+|[\w.-]+/[\w.-]+)$")

_ISO_DATE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: OKF fields whose semantics Never4gA adopts unchanged (core/02 section 2.1) and
#: which therefore get checked even at `okf` level.
_OKF_LEVEL_FIELDS: Final = ("status", "sources", "generated", "verified")


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationLevel(StrEnum):
    """core/02 section 2.3."""

    #: Applicable OKF v0.2 requirements only.
    OKF = "okf"
    #: Never4gA base metadata and type placement.
    CORE = "core"
    #: Registered type/profile constraints, controlled vocabularies, relation
    #: targets where resolvable, lifecycle, provenance, placement, extensions.
    STRICT = "strict"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: Severity
    field: str | None = None
    repair_hint: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    path: VaultPath
    level: ValidationLevel
    issues: tuple[ValidationIssue, ...]

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        """True when nothing is *wrong*. Warnings do not make a document invalid."""
        return not self.errors


class _Collector:
    def __init__(self, level: ValidationLevel) -> None:
        self.level = level
        self.issues: list[ValidationIssue] = []

    def error(self, code: str, message: str, field: str | None, hint: str) -> None:
        self.issues.append(ValidationIssue(code, message, Severity.ERROR, field, hint))

    def warn(self, code: str, message: str, field: str | None, hint: str) -> None:
        self.issues.append(ValidationIssue(code, message, Severity.WARNING, field, hint))

    @property
    def strict(self) -> bool:
        return self.level is ValidationLevel.STRICT

    @property
    def at_least_core(self) -> bool:
        return self.level in (ValidationLevel.CORE, ValidationLevel.STRICT)


def validate_document(
    path: VaultPath,
    frontmatter: Mapping[str, Any] | None,
    *,
    level: ValidationLevel = ValidationLevel.CORE,
    role: DocumentRole | None = None,
    known_ids: Iterable[ConceptId] | None = None,
    domains: Collection[str] | None = None,
) -> ValidationReport:
    """Validate one document's frontmatter against Schema v0.1.

    ``role`` defaults to what the path implies; supply it when the caller has
    already classified the document. ``known_ids`` enables relation-target
    resolution -- omit it and unresolvable targets are simply not checked, since
    a single-document validation cannot know what else the vault holds
    (core/02 section 35, "Relationships").

    ``domains`` is the registered domain vocabulary -- normally what the vault's
    own Domain Registry declares (core/02 section 22). Omitted, the bootstrap
    :data:`DOMAIN_VALUES` apply, which is what a vault without a registry gets.
    """
    collector = _Collector(level)
    document_role = role if role is not None else role_of(path)
    resolvable = frozenset(known_ids) if known_ids is not None else None

    if _check_reserved_document(collector, document_role, frontmatter):
        return ValidationReport(path, level, tuple(collector.issues))

    if frontmatter is None:
        collector.error(
            "missing_frontmatter",
            "a canonical concept requires YAML frontmatter",
            None,
            "add a frontmatter block with type, id, schema, title and created_at",
        )
        return ValidationReport(path, level, tuple(collector.issues))

    _check_required_fields(collector, frontmatter)
    _check_okf_fields(collector, frontmatter)

    if collector.at_least_core:
        spec = type_spec(frontmatter.get("type"))
        _check_type(collector, frontmatter)
        _check_identity(collector, frontmatter)
        _check_schema_version(collector, frontmatter)
        _check_timestamps(collector, frontmatter)
        _check_booleans(collector, frontmatter)
        _check_scope_references(collector, frontmatter)
        _check_type_requirements(collector, path, frontmatter, spec)
        _check_placement(collector, path, frontmatter, spec)

    _check_registry_document(collector, frontmatter)

    if collector.strict:
        _check_vocabularies(collector, frontmatter)
        _check_lifecycle(collector, frontmatter, type_spec(frontmatter.get("type")))
        _check_relations(collector, frontmatter, resolvable)
        _check_profiles(collector, frontmatter)
        _check_extensions(collector, frontmatter)
        _check_classification(collector, frontmatter, domains)
        _check_unregistered_fields(collector, frontmatter)

    return ValidationReport(path, level, tuple(collector.issues))


def _check_reserved_document(
    collector: _Collector,
    role: DocumentRole,
    frontmatter: Mapping[str, Any] | None,
) -> bool:
    """Reserved OKF files are not concepts. Returns True when checking should stop.

    core/02 sections 3.2 and 4: except for the bundle root -- which may declare
    ``okf_version`` -- ``index.md`` must not carry concept frontmatter, and
    ``log.md`` is directory history rather than an activity record.
    """
    if role is DocumentRole.CONCEPT:
        return False
    if role is DocumentRole.FOREIGN_FORMAT:
        # Governed by another standard; concept validation does not apply
        # (core/02 sections 2.2 and 3.3).
        return True
    if not frontmatter:
        return True

    allowed = {"okf_version"} if role is DocumentRole.ROOT_INDEX else set()
    offending = sorted(set(frontmatter) - allowed)
    if offending:
        collector.error(
            "reserved_document_has_frontmatter",
            f"{role.value} is reserved OKF navigation/history and must not carry "
            f"concept frontmatter: {', '.join(offending)}",
            None,
            "move the concept to a normally named file; index.md and log.md are "
            "reserved (core/01 sections 4 and 13)",
        )
    return True


def _check_required_fields(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    required = ("type",) if collector.level is ValidationLevel.OKF else REQUIRED_BASE_FIELDS
    for name in required:
        if name not in frontmatter:
            collector.error(
                "missing_required_field",
                f"required field {name!r} is absent",
                name,
                f"add {name} (core/02 section 5.1)",
            )


def _check_type(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    value = frontmatter.get("type")
    if value is None:
        return
    if not isinstance(value, str) or not _SNAKE_CASE.match(value):
        collector.error(
            "invalid_type",
            f"type must be lowercase snake_case, got {value!r}",
            "type",
            "rename the type to lowercase snake_case (core/02 section 5.1)",
        )
        return
    if collector.strict and type_spec(value) is None:
        collector.warn(
            "unregistered_type",
            f"type {value!r} is not in the v0.1 Type Registry",
            "type",
            "register the type, or keep it -- unknown types are tolerated (core/02 section 5.1)",
        )


def _check_identity(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    value = frontmatter.get("id")
    if value is None:
        return
    if not isinstance(value, str) or not CANONICAL_UUID_PATTERN.match(value):
        collector.error(
            "invalid_id",
            f"id must be a canonical lowercase hyphenated UUID, got {value!r}",
            "id",
            "use a UUIDv7 in canonical form; a backend row ID is never canonical "
            "identity (core/02 section 5.1, core/06 section 3)",
        )
        return
    try:
        parsed = ConceptId.parse(value)
    except Exception:
        parsed = None
    if parsed is None or parsed.value.version != UUID_VERSION:
        collector.error(
            "invalid_id",
            f"id must be UUID version {UUID_VERSION}, got {value!r}",
            "id",
            f"mint a UUIDv{UUID_VERSION} (core/02 section 5.1)",
        )


def _check_schema_version(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    value = frontmatter.get("schema")
    if value is not None and value != SCHEMA_VERSION:
        collector.warn(
            "unrecognised_schema",
            f"schema {value!r} is not {SCHEMA_VERSION}",
            "schema",
            "process what is understood and report reduced validation rather than "
            "rejecting the document (core/02 section 31)",
        )


def _is_offset_datetime(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _check_timestamp(collector: _Collector, field: str, value: object) -> None:
    if _is_offset_datetime(value):
        return
    collector.error(
        "invalid_timestamp",
        f"{field} must be an ISO 8601 datetime with an explicit offset, got {value!r}",
        field,
        'use a quoted value such as "2026-08-22T19:00:00Z" (core/02 section 7.4)',
    )


def _check_timestamps(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    for name in TIMESTAMP_FIELDS:
        if name in frontmatter:
            _check_timestamp(collector, name, frontmatter[name])
    for name in DATE_FIELDS:
        if name not in frontmatter:
            continue
        value = frontmatter[name]
        valid = isinstance(value, date) and not isinstance(value, datetime)
        if isinstance(value, str):
            valid = bool(_ISO_DATE.match(value))
        if not valid:
            collector.error(
                "invalid_date",
                f"{name} must be a YYYY-MM-DD date, got {value!r}",
                name,
                'use a quoted value such as "2026-09-01" (core/02 sections 21.8, 21.10)',
            )


def _check_reference(
    collector: _Collector, field: str, value: object, code: str, what: str
) -> None:
    if not isinstance(value, str) or not CANONICAL_UUID_PATTERN.match(value):
        collector.error(
            code,
            f"{field} must be a stable Never4gA UUID, got {value!r}",
            field,
            f"reference {what} by its frontmatter id, not by title or path (core/02 section 16)",
        )


def _check_scope_references(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    if "workspace" in frontmatter:
        _check_reference(
            collector,
            "workspace",
            frontmatter["workspace"],
            "invalid_workspace_reference",
            "the owning workspace",
        )
    if "parent" in frontmatter:
        _check_reference(
            collector,
            "parent",
            frontmatter["parent"],
            "invalid_parent_reference",
            "the parent object",
        )
    if AREA_SCOPE_FIELD in frontmatter:
        _check_reference(
            collector,
            AREA_SCOPE_FIELD,
            frontmatter[AREA_SCOPE_FIELD],
            "invalid_area_reference",
            "the owning life area",
        )
        if "workspace" in frontmatter:
            # core/02 section 16.3. A concept has one scope, and section 16.1
            # forbids guessing which of two disagreeing sources is authoritative
            # -- so this is reported rather than ranked.
            collector.error(
                "conflicting_scope_fields",
                "a concept carries one scope, and this one declares both `workspace` and `area`",
                AREA_SCOPE_FIELD,
                "keep the field that matches where the document sits and remove "
                "the other (core/02 section 16.3)",
            )


def _check_type_requirements(
    collector: _Collector,
    path: VaultPath,
    frontmatter: Mapping[str, Any],
    spec: TypeSpec | None,
) -> None:
    """core/02 section 21, and section 16.3 for where the scope field comes from.

    Requirement follows placement: a document in a life area carries `area`
    where a workspace-placed one carries `workspace`. Nothing else about the
    type changes.
    """
    if spec is None:
        return
    for name in spec.required_fields_for(path):
        if name not in frontmatter:
            collector.error(
                "missing_required_field",
                f"type {spec.name!r} requires {name!r}",
                name,
                f"add {name} (core/02 section 21)",
            )


def _check_placement(
    collector: _Collector,
    path: VaultPath,
    frontmatter: Mapping[str, Any],
    spec: TypeSpec | None,
) -> None:
    """core/02 section 23 and core/01 sections 5, 8 and 9.

    Two distinct rules. A *misplaced* document sits where its type does not
    belong. A *nested* one sits inside an area that must stay flat, which is a
    structural rule independent of type.
    """
    area = flat_area_of(path)
    if area is not None and len(path.segments) > 3:
        collector.error(
            "nested_flat_area",
            f"{area.value} must stay flat; {path} introduces a folder hierarchy",
            None,
            "classify with type, domains, tags, links, typed relations and Maps "
            "instead of folders (core/01 sections 8 and 9)",
        )

    if spec is None:
        # Never4gA cannot know where an unregistered type belongs.
        return
    if path.segments[0] == "00_Inbox":
        # Inbox is unprocessed by definition (core/01 section 5).
        return
    if spec.accepts(path):
        return

    expected = ", ".join(location.value or location.kind.value for location in spec.locations)
    message = f"type {spec.name!r} does not belong at {path}; expected {expected}"
    hint = (
        "repair explicitly: change the type, move the file, or register an "
        "extension. Never4gA does not rewrite user organization "
        "(core/02 section 23)"
    )
    if collector.strict and spec.location_is_binding:
        collector.error("misplaced_document", message, None, hint)
    else:
        collector.warn("misplaced_document", message, None, hint)


def _check_okf_fields(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    """OKF v0.2 semantics Never4gA adopts unchanged (core/02 sections 2.1, 13, 14)."""
    status = frontmatter.get("status")
    if status is not None and status not in STATUS_VALUES:
        collector.error(
            "invalid_status",
            f"status must be one of {', '.join(STATUS_VALUES)}, got {status!r}",
            "status",
            "status describes document maturity; use `lifecycle` for the state of "
            "the object itself (core/02 sections 10 and 11)",
        )

    sources = frontmatter.get("sources")
    if sources is not None:
        if not isinstance(sources, Sequence) or isinstance(sources, str):
            collector.error(
                "invalid_sources",
                "sources must be a list of entries",
                "sources",
                "use a YAML list of mappings (core/02 section 13)",
            )
        else:
            for index, entry in enumerate(sources):
                if not isinstance(entry, Mapping) or "resource" in entry:
                    continue
                collector.error(
                    "source_missing_resource",
                    f"sources[{index}] has no resource",
                    "sources",
                    "each source entry MUST contain resource (core/02 section 13)",
                )

    _check_actor_block(collector, frontmatter.get("generated"), "generated")
    verified = frontmatter.get("verified")
    if verified is not None:
        if not isinstance(verified, Sequence) or isinstance(verified, str):
            collector.error(
                "invalid_verified",
                "verified must be a list of verification entries",
                "verified",
                "use a YAML list of mappings with by and at (core/02 section 14.2)",
            )
        else:
            for entry in verified:
                _check_actor_block(collector, entry, "verified")


def _check_actor_block(collector: _Collector, block: object, field: str) -> None:
    if block is None:
        return
    if not isinstance(block, Mapping):
        collector.error(
            f"invalid_{field}",
            f"{field} must be a mapping with by and at",
            field,
            "use the OKF shape: by: <actor>, at: <timestamp> (core/02 section 14.1)",
        )
        return
    if "by" not in block or "at" not in block:
        collector.error(
            f"incomplete_{field}",
            f"{field} requires both by and at",
            field,
            "Never4gA must not fabricate a timestamp it cannot substantiate (core/02 section 5.2)",
        )
        return
    actor = block["by"]
    if not isinstance(actor, str) or not _ACTOR.match(actor):
        collector.error(
            "invalid_actor",
            f"{field}.by must be human:<id>, process:<id> or <producer>/<version>, got {actor!r}",
            field,
            "use an OKF actor form; do not embed email addresses (core/02 section 14.1)",
        )
    _check_timestamp(collector, f"{field}.at", block["at"])


def _check_vocabularies(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    authority = frontmatter.get("authority")
    if authority is not None and authority not in AUTHORITY_VALUES:
        collector.error(
            "invalid_authority",
            f"authority must be one of {', '.join(AUTHORITY_VALUES)}, got {authority!r}",
            "authority",
            "authority is an operational role, not a confidence score (core/02 section 12)",
        )

    priority = frontmatter.get("priority")
    if priority is not None and priority not in PRIORITY_VALUES:
        collector.error(
            "invalid_priority",
            f"priority must be one of {', '.join(PRIORITY_VALUES)}, got {priority!r}",
            "priority",
            "use the registered priority vocabulary (core/02 section 21.10)",
        )

    workspace_type = frontmatter.get("workspace_type")
    if workspace_type is not None and workspace_type not in WORKSPACE_TYPE_VALUES:
        collector.warn(
            "unregistered_workspace_type",
            f"workspace_type {workspace_type!r} is not registered",
            "workspace_type",
            "the workspace type registry is extensible (core/03 section 7.1)",
        )


def _check_lifecycle(
    collector: _Collector, frontmatter: Mapping[str, Any], spec: TypeSpec | None
) -> None:
    """core/02 section 11: lifecycle vocabularies are type-specific."""
    lifecycle = frontmatter.get("lifecycle")
    if lifecycle is None or spec is None or not spec.lifecycle_values:
        return
    if lifecycle not in spec.lifecycle_values:
        collector.error(
            "invalid_lifecycle",
            f"lifecycle {lifecycle!r} is not in the {spec.name} vocabulary "
            f"({', '.join(spec.lifecycle_values)})",
            "lifecycle",
            "use the vocabulary registered for this type (core/02 section 11)",
        )


def _check_booleans(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    """A boolean field holds `true` or `false`, and a string spelling it is not one.

    `required_reading: "true"` would read as set to anything that tests for a
    value, and as unset to anything that tests for `True`, so it is reported
    rather than interpreted (core/02 section 21.15).
    """
    for name in BOOLEAN_FIELDS:
        if name in frontmatter and not isinstance(frontmatter[name], bool):
            collector.error(
                f"invalid_{name}",
                f"{name} must be true or false, got {frontmatter[name]!r}",
                name,
                f"write {name}: true or {name}: false, unquoted (core/02 section 21.15)",
            )


def _check_relations(
    collector: _Collector,
    frontmatter: Mapping[str, Any],
    known_ids: frozenset[ConceptId] | None,
) -> None:
    """core/02 section 17."""
    relations = frontmatter.get("relations")
    if relations is None:
        return
    if not isinstance(relations, Sequence) or isinstance(relations, str):
        collector.error(
            "invalid_relations",
            "relations must be a list of {type, target} mappings",
            "relations",
            "use a YAML list (core/02 section 17)",
        )
        return

    for index, relation in enumerate(relations):
        if not isinstance(relation, Mapping):
            collector.error(
                "invalid_relations",
                f"relations[{index}] is not a mapping",
                "relations",
                "each relation is a mapping with type and target (core/02 section 17.1)",
            )
            continue
        if "type" not in relation or "target" not in relation:
            collector.error(
                "incomplete_relation",
                f"relations[{index}] requires both type and target",
                "relations",
                "a relation names a registered type and a stable UUID target "
                "(core/02 section 17.1)",
            )
            continue

        relation_type = relation["type"]
        if relation_type not in RELATION_TYPES:
            collector.warn(
                "unregistered_relation_type",
                f"relation type {relation_type!r} is not in the core vocabulary",
                "relations",
                "register new relation types; unknown ones must be preserved "
                "(core/02 section 17.3)",
            )

        target = relation["target"]
        if not isinstance(target, str) or not CANONICAL_UUID_PATTERN.match(target):
            collector.error(
                "invalid_relation_target",
                f"relations[{index}].target must be a stable UUID, got {target!r}",
                "relations",
                "typed relations use IDs; Markdown links carry the readable path "
                "(core/02 section 18)",
            )
            continue

        if known_ids is not None and ConceptId.parse(target) not in known_ids:
            collector.error(
                "unresolved_relation_target",
                f"relations[{index}].target {target} resolves to no concept",
                "relations",
                "a missing target is reportable without corrupting the document; "
                "fix or remove the relation (core/02 section 35)",
            )


def _check_profiles(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    """core/02 section 19."""
    profiles = frontmatter.get("profiles")
    if profiles is None:
        return
    if not isinstance(profiles, Sequence) or isinstance(profiles, str):
        collector.error(
            "invalid_profiles",
            "profiles must be a list of profile identifiers",
            "profiles",
            "use a YAML list of strings (core/02 section 19.1)",
        )
        return
    for profile in profiles:
        if profile not in KNOWN_PROFILES:
            collector.warn(
                "unregistered_profile",
                f"profile {profile!r} is not known to this implementation",
                "profiles",
                "unknown profiles MUST be preserved; report reduced validation "
                "capability rather than rejecting the concept "
                "(core/02 section 19.2)",
            )


def _check_extensions(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    """core/02 section 20: extension data is namespaced."""
    extensions = frontmatter.get("extensions")
    if extensions is None:
        return
    if not isinstance(extensions, Mapping):
        collector.error(
            "invalid_extensions",
            "extensions must be a mapping of namespace to data",
            "extensions",
            "nest producer-specific metadata under a distinctive namespace (core/02 section 20)",
        )
        return
    for namespace, value in extensions.items():
        if not isinstance(value, Mapping):
            collector.error(
                "invalid_extensions",
                f"extensions.{namespace} must be a mapping; extension data is "
                "namespaced, not placed directly under extensions",
                "extensions",
                "write extensions.<namespace>.<key> (core/02 section 20)",
            )


def _check_registry_document(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    """core/02 section 21.17: a registry's values are its whole point.

    A malformed registry is worse than a missing one -- consumers fall back to
    the bootstrap vocabulary either way, but only this error says *why* the
    curated list is not in effect. Checked at every level, not just strict,
    because a broken registry silently changes what every other document
    validates against.
    """
    if frontmatter.get("type") != "registry":
        return
    values = frontmatter.get("values")
    if values is None:
        return
    if isinstance(values, str) or not isinstance(values, Sequence):
        collector.error(
            "invalid_registry_values",
            "values must be a list",
            "values",
            "write values as a YAML list even when it holds one entry",
        )
        return
    if not all(isinstance(value, str) for value in values):
        collector.error(
            "invalid_registry_values",
            "every registry value must be a string",
            "values",
            "quote each value; a registry holds identifiers, not structures",
        )


def _check_classification(
    collector: _Collector,
    frontmatter: Mapping[str, Any],
    domains: Collection[str] | None = None,
) -> None:
    """core/02 section 9."""
    registered = DOMAIN_VALUES if domains is None else domains
    for name in ("tags", "domains", "aliases"):
        value = frontmatter.get(name)
        if value is None:
            continue
        if isinstance(value, str) or not isinstance(value, Sequence):
            collector.error(
                "invalid_list_field",
                f"{name} must be a list",
                name,
                f"write {name} as a YAML list even when it holds one value",
            )
            continue
        if name == "domains":
            for domain in value:
                if domain not in registered:
                    collector.warn(
                        "unregistered_domain",
                        f"domain {domain!r} is not in the Domain Registry",
                        "domains",
                        "domains are broad, stable and controlled; register the "
                        "value or use a tag instead (core/02 section 9.1)",
                    )
        if name == "tags":
            for tag in value:
                if isinstance(tag, str) and tag != tag.lower():
                    collector.warn(
                        "unnormalised_tag",
                        f"tag {tag!r} is not a normalized lowercase identifier",
                        "tags",
                        "tags SHOULD use normalized lowercase identifiers (core/02 section 9.2)",
                    )


def _check_unregistered_fields(collector: _Collector, frontmatter: Mapping[str, Any]) -> None:
    """core/02 sections 20 and 32.

    Unknown fields are preserved -- always -- but at strict level they are worth
    naming, because unregistered vocabulary proliferation is a first-class
    maintenance concern.
    """
    spec = type_spec(frontmatter.get("type"))
    registered = {
        *REQUIRED_BASE_FIELDS,
        *BASE_OPTIONAL_FIELDS,
        # A field a type *requires* is registered by definition. Without this,
        # a type with a required field of its own would warn about its own
        # required frontmatter.
        *(spec.required_fields if spec else ()),
        *(spec.extra_fields if spec else ()),
    }
    for name in frontmatter:
        if name in registered:
            continue
        collector.warn(
            "unregistered_field",
            f"field {name!r} is not part of Schema v0.1",
            name,
            "namespaced extensions belong under extensions.<namespace>; the field "
            "is preserved either way (core/02 sections 20 and 31)",
        )
