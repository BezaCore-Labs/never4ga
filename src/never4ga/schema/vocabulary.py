"""Controlled vocabularies and field names of Schema v0.1 (core/02).

core/02 section 22 names six registries. Section 22 also permits initial
bootstrapping to hard-code the core values the specification defines, which is
what this module does. A vault being initialised has no registry to read *from*
yet (`50_System/Schemas/` is created by the very service that would need it), so
these values are the fallback; a vault's own `registry` concepts take over where
they exist.

Openness differs per registry, and the difference is deliberate:

- ``STATUS_VALUES`` and ``AUTHORITY_VALUES`` are closed. They are core semantics
  with defined meaning; an unknown value is an error.
- ``WORKSPACE_TYPE_VALUES``, ``DOMAIN_VALUES`` and
  ``RELATION_TYPES`` are open. Section 21.6 says the entity list "is not closed",
  section 9.1 makes unknown domains "a strict-validation warning until
  registered", and section 17.3 requires unknown relation types to be preserved.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = [
    "AUTHORITY_VALUES",
    "BASE_OPTIONAL_FIELDS",
    "BASE_WORKSPACE_PROFILE",
    "BOOLEAN_FIELDS",
    "DATE_FIELDS",
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
    "WORKSPACE_TYPE_VALUES",
    "coerce_field",
    "field_kind",
]

#: core/02 section 5.1.
SCHEMA_VERSION: Final = "never4ga/0.1"

#: Required on every canonical concept document (core/02 section 5.1).
REQUIRED_BASE_FIELDS: Final = ("type", "id", "schema", "title", "created_at")

#: core/02 section 6, plus the recommended fields of section 5.2. Everything not
#: listed here and not required is an unregistered field: preserved, and noted at
#: strict level so vocabulary drift stays visible (core/02 section 32).
BASE_OPTIONAL_FIELDS: Final = (
    "description",
    "generated",
    "aliases",
    "resource",
    "tags",
    "domains",
    "status",
    "stale_after",
    "verified",
    "sources",
    "usage_window",
    "authority",
    "lifecycle",
    "parent",
    "workspace",
    # The scope field an area-placed document carries in place of `workspace`
    # (core/02 section 16.3). An area is not a workspace.
    "area",
    "relations",
    "profiles",
    "extensions",
)

#: Fields core/02 spells as a YAML list, and which validation rejects as a bare
#: scalar. A caller that supplies one value still means a list of one.
LIST_VALUED_FIELDS: Final = (
    "aliases",
    "tags",
    "domains",
    "sources",
    "verified",
    "relations",
    "profiles",
    "values",
)

#: Fields that must be a full ISO 8601 datetime with an explicit offset
#: (core/02 section 7.4).
TIMESTAMP_FIELDS: Final = ("created_at", "stale_after", "occurred_at")

#: Fields the specification spells "YYYY-MM-DD" (core/02 sections 21.8, 21.10,
#: 21.24, 21.25).
DATE_FIELDS: Final = ("target_date", "due_date", "opens", "due")

#: Fields whose value is a whole number when it is a number at all.
#:
#: `unit` is core/02's "number-or-label": Unit 3 is an integer and "Final" is
#: not, so this declares what a *convertible* value means rather than
#: promising every value converts. `grade` is deliberately absent -- an A, a
#: 95, a 95/100 and a Pass are all grades, and declaring it numeric would
#: assert a vocabulary it does not have.
INTEGER_FIELDS: Final = ("unit",)

#: Fields that are `true` or `false` and nothing else. `required_reading` says
#: whether every startup a standard applies to reads it in full (core/02
#: section 21.15). Only the two YAML 1.2 spellings convert: `yes`, `on` and
#: `True` mean a boolean to one YAML version and a string to another.
BOOLEAN_FIELDS: Final = ("required_reading",)

#: core/02 section 10, preserving OKF v0.2 semantics exactly. Closed.
STATUS_VALUES: Final = ("draft", "stable", "deprecated")

#: core/02 section 12. Closed: these are core semantics, not a taxonomy.
AUTHORITY_VALUES: Final = ("authoritative", "informational", "provisional", "derived")

#: core/02 section 21.10.
PRIORITY_VALUES: Final = ("low", "normal", "high", "critical")

#: core/02 section 17.2. Open -- new types require registration, and unknown ones
#: must survive (section 17.3).
RELATION_TYPES: Final = (
    "related_to",
    "depends_on",
    "blocks",
    "implements",
    "supports",
    "applies_to",
    "superseded_by",
    # Written on a record of completion, naming the plan it finished.
    "closes",
    # Written on a child workspace's own standard, naming the inherited one it
    # overrules (core/03 section 32). Only an explicit exception counts;
    # silence never overrides.
    "excepts",
)

#: core/03 section 7.1. Extensible.
WORKSPACE_TYPE_VALUES: Final = (
    "organization",
    "product",
    "project",
    "initiative",
    "research",
    "event",
    "personal_project",
    "ministry",
    "operations",
)

#: The Domain Registry of core/02 section 9.1 is a vault artefact the user
#: curates, so v0.1 ships the handful of domains the specifications themselves
#: use as examples and warns about anything else rather than inventing a
#: taxonomy nobody asked for.
DOMAIN_VALUES: Final = (
    "software_development",
    "artificial_intelligence",
    "home_maintenance",
)

#: core/03 section 8: every workspace carries this one.
BASE_WORKSPACE_PROFILE: Final = "never4ga/workspace/base/0.1"

#: core/02 section 19.1, core/03 sections 8 to 13 and core/05 section 6.
KNOWN_PROFILES: Final = (
    "never4ga/core/0.1",
    "never4ga/workspace/base/0.1",
    "never4ga/workspace/organization/0.1",
    "never4ga/workspace/software/0.1",
    "never4ga/workspace/research/0.1",
    "never4ga/workspace/event/0.1",
    "never4ga/workspace/work_managed/0.1",
)


def field_kind(name: str) -> str | None:
    """What a registered field is, or ``None`` when nothing declares it.

    The registry publishes this so a client can render the right input, and
    the CLI uses it to decide what a `--field` string may safely become.
    """
    if name in LIST_VALUED_FIELDS:
        return "list"
    if name in TIMESTAMP_FIELDS:
        return "timestamp"
    if name in DATE_FIELDS:
        return "date"
    if name in INTEGER_FIELDS:
        return "integer"
    if name in BOOLEAN_FIELDS:
        return "boolean"
    return None


def coerce_field(name: str, value: Any) -> Any:
    """Convert a `--field` string to what the vocabulary says the field is.

    **Only a declared field is converted, and only when the value really is
    one.** Without conversion, `--field unit=1` would write `unit: '1'` while
    the same field typed by hand is an integer, and two notes of one type would
    disagree about the type of the property a Base sorts by.

    Nothing is guessed. A verb that guessed would have to rule on whether `1.0`
    is a number and `no` is a boolean (`no` being Norway, which is why core/02
    quotes its timestamps). The vocabulary names the field, the field names its
    kind, and everything undeclared stays the string it arrived as.

    Dates and timestamps keep their string form on purpose: core/02 section
    7.4 quotes them because a bare `YYYY-MM-DD` is a date *object* to YAML and
    the vault stores the text. Declaring them still matters -- a client
    rendering a date picker needs to know -- but the conversion is a
    validation question, not a retyping one.
    """
    if not isinstance(value, str):
        return value
    if name in INTEGER_FIELDS:
        try:
            return int(value)
        except ValueError:
            # "Final" is a legal unit. A label is not a failure to be a number.
            return value
    if name in BOOLEAN_FIELDS and value in ("true", "false"):
        return value == "true"
    return value
