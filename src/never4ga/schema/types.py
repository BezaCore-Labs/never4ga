"""The Type Registry of core/02 section 21.

Each entry states what a type requires beyond the base schema, which lifecycle
vocabulary governs it, and where documents of that type belong. Placement is
expressed against :mod:`never4ga.layout` rather than as raw path strings, so a
type that lives in "a workspace's Goals directory" keeps working for child
workspaces and archived material without restating either rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from never4ga.domain.document import VaultPath
from never4ga.layout import (
    AREA_MANIFEST,
    HOME,
    RESERVED_INDEX,
    SYSTEM_MANIFEST,
    WORKSPACE_MANIFEST,
    VaultRoot,
    is_life_area_content,
    life_area_directory_of,
    workspace_directory_of,
    workspace_section_of,
)

__all__ = [
    "AREA_SCOPE_FIELD",
    "TYPE_REGISTRY",
    "Location",
    "LocationKind",
    "TypeSpec",
    "effective_path",
    "type_spec",
]

#: How a `90_Archive/<Section>/` path maps back onto the root it was detached
#: from (core/01 section 11). Archiving relocates material without retyping it,
#: so placement is judged against the pre-archive location.
_ARCHIVE_SECTION_ROOTS: Final = {
    "Workspaces": VaultRoot.WORKSPACES,
    "Life": VaultRoot.LIFE,
    "Knowledge": VaultRoot.KNOWLEDGE,
    "System": VaultRoot.SYSTEM,
}


def effective_path(path: VaultPath) -> VaultPath:
    """The path placement rules are judged against.

    Archived material is judged where it came from: `90_Archive/Knowledge/Notes/
    Old.md` is a correctly placed knowledge note, not a misfiled one.
    """
    segments = path.segments
    if segments[0] != VaultRoot.ARCHIVE or len(segments) < 3:
        return path
    root = _ARCHIVE_SECTION_ROOTS.get(segments[1])
    if root is None:
        return path
    return VaultPath((str(root), *segments[2:]))


class LocationKind(StrEnum):
    """How a type's canonical location is expressed."""

    #: One exact vault path, such as `home.md`.
    EXACT_PATH = "exact_path"
    #: Directly inside a named directory, such as `30_Knowledge/Notes`.
    DIRECTORY = "directory"
    #: The `workspace.md` of any workspace, at any nesting depth.
    WORKSPACE_MANIFEST = "workspace_manifest"
    #: The `area.md` of any life area.
    LIFE_AREA_MANIFEST = "life_area_manifest"
    #: A named section inside any workspace, such as `Goals`.
    WORKSPACE_SECTION = "workspace_section"
    #: Anywhere inside a life area's own subtree, at any depth (core/01 section 7).
    LIFE_AREA_CONTENT = "life_area_content"


@dataclass(frozen=True, slots=True)
class Location:
    """One place documents of a type may live."""

    kind: LocationKind
    value: str = ""

    def accepts(self, path: VaultPath) -> bool:
        candidate = effective_path(path)
        match self.kind:
            case LocationKind.EXACT_PATH:
                return str(candidate) == self.value
            case LocationKind.DIRECTORY:
                return (
                    "/".join(candidate.segments[:-1]) == self.value
                    and candidate.name != RESERVED_INDEX
                )
            case LocationKind.WORKSPACE_MANIFEST:
                workspace = workspace_directory_of(candidate)
                return workspace is not None and candidate.segments == (
                    *workspace.segments,
                    WORKSPACE_MANIFEST,
                )
            case LocationKind.LIFE_AREA_MANIFEST:
                area = life_area_directory_of(candidate)
                return area is not None and candidate.segments == (
                    *area.segments,
                    AREA_MANIFEST,
                )
            case LocationKind.WORKSPACE_SECTION:
                return workspace_section_of(candidate) == self.value
            case LocationKind.LIFE_AREA_CONTENT:
                return is_life_area_content(candidate)


@dataclass(frozen=True, slots=True)
class TypeSpec:
    """What core/02 section 21 says about one registered type."""

    name: str
    locations: tuple[Location, ...]
    #: Required in addition to :data:`REQUIRED_BASE_FIELDS`.
    required_fields: tuple[str, ...] = ()
    #: The type-specific lifecycle vocabulary (core/02 section 11). Empty when the
    #: type declares none, in which case any value is tolerated.
    lifecycle_values: tuple[str, ...] = ()
    #: False for the two types core/02 spells "Typical location", where a
    #: mismatch is advice rather than an error.
    location_is_binding: bool = True
    #: core/02's "Recommended authority", carried so templates and `doctor` can
    #: suggest one without hard-coding it at the call site.
    recommended_authority: str | None = None
    #: Fields registered for this type beyond the base optional set.
    extra_fields: tuple[str, ...] = field(default=())

    def accepts(self, path: VaultPath) -> bool:
        return any(location.accepts(path) for location in self.locations)

    @property
    def creatable(self) -> bool:
        """Whether the generic creation verb makes one of these.

        False for the two manifest types, which make a directory and an
        `index.md` beside them, and for the two singletons pinned to one exact
        path -- `init` writes those and nothing else may. A surface offering a
        list of types must not offer one the verb will refuse.

        The reason each is refused lives with the refusal in
        `services.creation`, which says which verb to use instead. The *fact*
        lives here so a client can ask without provoking an error, and
        `test_the_registry_and_the_verb_agree_on_what_is_creatable` holds the
        two together.
        """
        return not any(
            location.kind
            in (
                LocationKind.WORKSPACE_MANIFEST,
                LocationKind.LIFE_AREA_MANIFEST,
                LocationKind.EXACT_PATH,
            )
            for location in self.locations
        )

    def required_fields_for(self, path: VaultPath) -> tuple[str, ...]:
        """What this type requires at ``path`` (core/02 section 16.3).

        An area is not a workspace, so a document placed in one cannot carry
        `workspace`. It carries `area` instead, and the requirement is
        *swapped* rather than added, because a concept has one scope.
        """
        if "workspace" not in self.required_fields or not is_life_area_content(
            effective_path(path)
        ):
            return self.required_fields
        return tuple(
            AREA_SCOPE_FIELD if name == "workspace" else name for name in self.required_fields
        )


def _directory(*segments: str) -> Location:
    return Location(LocationKind.DIRECTORY, "/".join(segments))


def _section(name: str) -> Location:
    return Location(LocationKind.WORKSPACE_SECTION, name)


#: The scope field an area-placed document carries in place of `workspace`
#: (core/02 section 16.3).
AREA_SCOPE_FIELD: Final = "area"

#: Where a type may live inside a life area. `knowledge` never uses it:
#: `30_Knowledge/Notes/` stays flat, and the line is practice versus reference
#: (core/01 section 7).
_IN_AN_AREA: Final = Location(LocationKind.LIFE_AREA_CONTENT)


#: The profile extension folders core/03 sections 9 and 10 define, split by
#: what a document in one does. Registering them is what lets a document be
#: legally placed in each.
#:
#: Architecture/ is absent deliberately: `documentation` already names it.
_PRESCRIBING_SECTIONS: Final = (
    "Strategy",
    "Brand",
    "Finance",
    "Operations",
    "Requirements",
    "Testing",
)
_PROFILE_SECTIONS: Final = (*_PRESCRIBING_SECTIONS, "Releases")


_SPECS: Final = (
    # 21.1 -- human-facing navigation/dashboard.
    TypeSpec(
        name="dashboard",
        locations=(Location(LocationKind.EXACT_PATH, str(HOME)),),
        location_is_binding=False,
        recommended_authority="informational",
    ),
    # 21.2 -- canonical workspace manifest.
    TypeSpec(
        name="workspace",
        locations=(Location(LocationKind.WORKSPACE_MANIFEST),),
        required_fields=("workspace_type", "lifecycle"),
        lifecycle_values=(
            "proposed",
            "active",
            "paused",
            "completed",
            "cancelled",
            "retired",
        ),
        extra_fields=("workspace_type", "repositories", "work_management", "cover"),
    ),
    # 21.3 -- life area manifest.
    TypeSpec(
        name="life_area",
        locations=(Location(LocationKind.LIFE_AREA_MANIFEST),),
        required_fields=("lifecycle",),
        lifecycle_values=("active", "retired"),
        recommended_authority="authoritative",
    ),
    # 21.4 -- durable reusable knowledge.
    TypeSpec(
        name="knowledge",
        locations=(_directory(VaultRoot.KNOWLEDGE, "Notes"),),
        recommended_authority="informational",
    ),
    # 21.5 -- curated navigation/synthesis.
    TypeSpec(
        name="map",
        locations=(_directory(VaultRoot.KNOWLEDGE, "Maps"),),
        recommended_authority="informational",
    ),
    # 21.6 was `entity`, retired along with `40_Entities/` (core/02 section
    # 21.6). An entity record was a knowledge note in a second place: a
    # LINK_RELATION edge does not read the target's type, and `document_names`
    # reads `aliases` from any frontmatter. The number is not reused.
    # 21.23 -- a person. Nobody writes a *knowledge note* about a person, so
    # people are the one thing with no other home.
    #
    # A life area, and only a life area (core/02 section 21.23): a lightweight
    # personal record, not a CRM. A person met through a club belongs to the
    # area that club is in. No `workspace` field: a person is not scoped to a
    # project, since one person can be a client's stakeholder, a lead for one
    # product and a member of a local club at once.
    TypeSpec(
        name="person",
        locations=(_IN_AN_AREA,),
        recommended_authority="informational",
    ),
    # 21.7 -- current-state/orientation material.
    TypeSpec(
        name="context",
        locations=(_section("Context"),),
        required_fields=("workspace",),
    ),
    # 21.8 -- desired outcome.
    TypeSpec(
        name="goal",
        locations=(_section("Goals"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("proposed", "active", "achieved", "abandoned"),
        extra_fields=("target_date",),
    ),
    # 21.9 -- plan. core/03 section 10 defines Releases/ as "release plans"
    # first, so a plan is at home in both.
    TypeSpec(
        name="plan",
        locations=(_section("Plans"), _section("Releases"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("draft", "active", "completed", "abandoned"),
    ),
    # 21.10 -- task.
    TypeSpec(
        name="task",
        locations=(_section("Tasks"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("todo", "in_progress", "blocked", "done", "cancelled"),
        extra_fields=("priority", "due_date"),
    ),
    # 21.11 -- durable decision.
    TypeSpec(
        name="decision",
        locations=(_section("Decisions"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("proposed", "accepted", "rejected", "superseded"),
    ),
    # 21.12 -- workspace-specific investigation.
    TypeSpec(
        name="research_note",
        locations=(_section("Research"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("active", "complete", "abandoned"),
        recommended_authority="provisional",
    ),
    # 21.24 -- one unit of a course. A course is a workspace, so the page a
    # student works a unit from is a document inside it and needs a type of
    # its own: `knowledge` is reference material in 30_Knowledge/,
    # `research_note` is research, and `resource` is the material that
    # *supports* the work rather than the work itself. The syllabus stays a
    # resource.
    TypeSpec(
        name="course_unit",
        locations=(_section("Units"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("not_started", "in_progress", "complete"),
        extra_fields=("unit", "opens", "due"),
    ),
    # 21.25 -- the graded work a unit carries. Prompts and rubrics are the
    # work's own record (due, submitted, graded), and filing them flat in
    # Resources/ would destroy the by-unit navigation Units/ exists for. The
    # syllabus and companion material stay resources.
    TypeSpec(
        name="course_assignment",
        locations=(_section("Units"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("not_started", "in_progress", "submitted", "graded"),
        extra_fields=("unit", "due", "grade"),
    ),
    # 21.13 -- supporting material record. "Typical location", so advisory,
    # but advisory still means a warning everywhere it is not listed, and
    # supporting material appears wherever the material it supports does. So it
    # is accepted in every profile section too.
    TypeSpec(
        name="resource",
        locations=(
            _section("Resources"),
            *(_section(name) for name in _PROFILE_SECTIONS),
            _IN_AN_AREA,
        ),
        location_is_binding=False,
    ),
    # 21.14 -- session/meeting/activity record. Never named log.md.
    TypeSpec(
        name="activity_log",
        locations=(_section("Logs"), _IN_AN_AREA),
        required_fields=("workspace", "occurred_at"),
        # `covers` is a roll-up's range. A log that carries it is a summary,
        # never the handoff a startup requires (core/07 section 10).
        extra_fields=("occurred_at", "covers"),
    ),
    # 21.15 -- standard. One in 50_System/Standards/ is vault-wide; one in a
    # workspace's profile folder is scoped to that workspace. A palette
    # specification, a bookkeeping cadence, a contract workflow, a requirement
    # and a testing strategy all prescribe, which is what a standard is -- the
    # location says who it binds. `required_reading` says when: whether every
    # startup it applies to reads it in full (core/07 section 10).
    TypeSpec(
        name="standard",
        locations=(
            _directory(VaultRoot.SYSTEM, "Standards"),
            *(_section(name) for name in _PRESCRIBING_SECTIONS),
        ),
        recommended_authority="authoritative",
        extra_fields=("required_reading",),
    ),
    # 21.16 -- schema governance description.
    TypeSpec(
        name="schema_definition",
        locations=(_directory(VaultRoot.SYSTEM, "Schemas"),),
        recommended_authority="authoritative",
    ),
    # 21.17 -- controlled vocabulary registry.
    TypeSpec(
        name="registry",
        locations=(_directory(VaultRoot.SYSTEM, "Schemas"),),
        recommended_authority="authoritative",
        # `registry` names the vocabulary, `values` holds it. core/02 section
        # 21.17 leaves the machine-readable fields to be registered; this is
        # that registration (see never4ga.schema.registries).
        extra_fields=("registry", "values"),
    ),
    # 21.18 -- documentation of anything the vault holds.
    TypeSpec(
        name="documentation",
        # Documentation of a project, a product or a workspace, not only of
        # Never4gA itself. Without it, a workspace wanting a README-equivalent
        # would have to borrow a type that means something else: `context`,
        # `knowledge` or `resource`. 50_System/Documentation/ is where
        # Never4gA documents itself.
        #
        # A workspace's Architecture/ (core/03 section 10: "design docs and
        # technical structure") holds a product's own specifications.
        # A workspace's own `Documentation/` is first because ordering decides
        # the default home when more than one candidate survives: creation
        # refuses to guess unless the winner is also `locations[0]`. With a
        # workspace named, that is this one; with none named, the system
        # directory is the only candidate left and there is nothing to guess.
        locations=(
            _section("Documentation"),
            _directory(VaultRoot.SYSTEM, "Documentation"),
            _section("Architecture"),
            _IN_AN_AREA,
        ),
        # No required scope, because one of its homes is not in one:
        # 50_System/Documentation/ is Never4gA describing itself to a vault.
        # Creation fills `workspace` in from
        # placement wherever placement implies one, which is how `resource`
        # has always worked.
    ),
    # 21.26 -- how to operate a thing.
    TypeSpec(
        name="runbook",
        # The registry covered design and governance -- decision, plan,
        # context, standard, requirement -- and reference -- knowledge,
        # resource. Nothing covered *operating*: start it, stop it, recover
        # it, what to do when it breaks, the sequence to run.
        #
        # Separate from `documentation` because retrieval has to tell them
        # apart: when something is broken, "what do I do when this breaks" is
        # a different need from "how is this built", and a type is how the
        # system knows which it is holding. Folding it in would make
        # `documentation` a catch-all.
        #
        # _IN_AN_AREA because operating a thing is not only a software
        # concern -- restarting the router is a runbook too.
        locations=(_section("Runbooks"), _IN_AN_AREA),
        required_fields=("workspace",),
    ),
    # 21.27 -- how a plan's phase was actually done.
    TypeSpec(
        name="walkthrough",
        # A plan says what to do and a log says what a session did, as events.
        # Neither says, step by step, what was done for a phase, with the exact
        # commands, why, and how it was checked -- which is what recreating
        # the work, or following it, needs.
        #
        # Separate from `runbook` because it is a record of one phase of one
        # plan rather than a procedure to repeat, and it has a lifecycle: it
        # is written while the phase runs and closed when the phase is done.
        # The startup pack names the one in progress (core/07 section 9), and
        # `wrap` holds a declared one to having changed (core/04 section 37).
        locations=(_section("Walkthroughs"), _IN_AN_AREA),
        required_fields=("workspace", "lifecycle"),
        lifecycle_values=("not_started", "in_progress", "complete"),
        # The phase as its plan names it, such as "3. The journey". Its link to
        # the plan is a relation, `implements`, not a field.
        extra_fields=("phase",),
    ),
    # 21.19 -- integration description/configuration.
    TypeSpec(
        name="integration",
        locations=(_directory(VaultRoot.SYSTEM, "Integrations"),),
        # The portable half of a connection (core/03 section 16): its
        # name, the adapter its `provider` field selects, where the instance
        # is if that is safe to share, and which project it points at.
        #
        # The secret half is deliberately *not* here and never will be. Leaving
        # `api_token` and its friends unregistered is not an oversight -- a
        # definition carrying one is refused outright by the connection
        # registry and reported by `doctor`, and registering the field would be
        # the first step towards it looking normal.
        extra_fields=("connection", "provider", "base_url", "project_ref"),
    ),
    # 21.20 -- the vault's own manifest; its id is the vault identity.
    # Reconciles section 21 with core/05 section 6.
    TypeSpec(
        name="system_manifest",
        locations=(Location(LocationKind.EXACT_PATH, str(SYSTEM_MANIFEST)),),
        recommended_authority="authoritative",
        # Which top-level directories `init` registered as foreign material
        # (core/02 section 3.3). Optional; a fresh vault has none.
        extra_fields=("foreign_material",),
    ),
    # 21.21 -- where a vendored Skill or Agent came from, and what arrived.
    TypeSpec(
        name="skill_provenance",
        # Beside `50_System/system.md` rather than inside `50_System/Skills/`.
        # The island the subject lives on is not validated or indexed (core/02
        # section 3.3), so a record kept there would be invisible to `doctor`
        # and to the index -- and whole-directory deployment would copy vault
        # bookkeeping into all three client trees.
        locations=(_directory(VaultRoot.SYSTEM),),
        required_fields=("skill_path", "origin", "integrity"),
        recommended_authority="informational",
        extra_fields=("upstream", "local"),
    ),
    # 21.22 -- a person's judgement that a maintenance finding is wrong, or is
    # right and does not matter.
    TypeSpec(
        name="finding_dismissal",
        # Beside `skill_provenance` in `50_System/`, and for its reason: this
        # is vault-level bookkeeping about the vault's own maintenance, and it
        # has to be validated and indexed rather than living on the
        # foreign-format island (core/02 section 3.3).
        locations=(_directory(VaultRoot.SYSTEM),),
        # `fingerprint` rather than a path, because the fingerprint is what the
        # ledger keys on and what re-detection compares -- a dismissal naming
        # only a path would silently cover a different finding about the same
        # file.
        required_fields=("finding_code", "fingerprint", "reason"),
        recommended_authority="informational",
        # Prefixed the way `skill_path` is: an unprefixed `path` in frontmatter
        # reads as the document's own.
        extra_fields=("finding_path",),
    ),
)

TYPE_REGISTRY: Final = {spec.name: spec for spec in _SPECS}


def type_spec(name: object) -> TypeSpec | None:
    """The registered spec for ``name``, or ``None`` if it is not registered.

    A miss is not an error: core/02 section 5.1 requires unknown type values to
    be tolerated.
    """
    if not isinstance(name, str):
        return None
    return TYPE_REGISTRY.get(name)
