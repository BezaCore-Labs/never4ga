"""Schema v0.1 -- the metadata contract of core/02.

Pure rules: what a canonical concept must carry, which vocabularies govern it,
and where each type belongs. No I/O, no YAML, no backend. Validation reports
issues; repair is always an explicit act (core/02 section 23).
"""

from __future__ import annotations

from never4ga.schema.freshness import is_stale, stale_instant
from never4ga.schema.registries import (
    DOMAIN_REGISTRY_KIND,
    domains_from_registry,
    registered_domains,
)
from never4ga.schema.types import (
    AREA_SCOPE_FIELD,
    TYPE_REGISTRY,
    Location,
    LocationKind,
    TypeSpec,
    effective_path,
    type_spec,
)
from never4ga.schema.validation import (
    Severity,
    ValidationIssue,
    ValidationLevel,
    ValidationReport,
    validate_document,
)
from never4ga.schema.vocabulary import (
    AUTHORITY_VALUES,
    BASE_OPTIONAL_FIELDS,
    BASE_WORKSPACE_PROFILE,
    BOOLEAN_FIELDS,
    DATE_FIELDS,
    DOMAIN_VALUES,
    INTEGER_FIELDS,
    KNOWN_PROFILES,
    LIST_VALUED_FIELDS,
    PRIORITY_VALUES,
    RELATION_TYPES,
    REQUIRED_BASE_FIELDS,
    SCHEMA_VERSION,
    STATUS_VALUES,
    TIMESTAMP_FIELDS,
    WORKSPACE_TYPE_VALUES,
    coerce_field,
    field_kind,
)

__all__ = [
    "AREA_SCOPE_FIELD",
    "AUTHORITY_VALUES",
    "BASE_OPTIONAL_FIELDS",
    "BASE_WORKSPACE_PROFILE",
    "BOOLEAN_FIELDS",
    "DATE_FIELDS",
    "DOMAIN_REGISTRY_KIND",
    "DOMAIN_VALUES",
    "INTEGER_FIELDS",
    "KNOWN_PROFILES",
    "LIST_VALUED_FIELDS",
    "PRIORITY_VALUES",
    "RELATION_TYPES",
    "REQUIRED_BASE_FIELDS",
    "SCHEMA_VERSION",
    "STATUS_VALUES",
    "TIMESTAMP_FIELDS",
    "TYPE_REGISTRY",
    "WORKSPACE_TYPE_VALUES",
    "Location",
    "LocationKind",
    "Severity",
    "TypeSpec",
    "ValidationIssue",
    "ValidationLevel",
    "ValidationReport",
    "coerce_field",
    "domains_from_registry",
    "effective_path",
    "field_kind",
    "is_stale",
    "registered_domains",
    "stale_instant",
    "type_spec",
    "validate_document",
]
