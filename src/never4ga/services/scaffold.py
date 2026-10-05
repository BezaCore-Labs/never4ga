"""The Markdown a fresh vault starts with.

Kept apart from the initialization logic so that "what a vault contains" stays
readable as prose. Every file here must be useful opened in any Markdown editor
with no plugin present (core/00 #6, details/obsidian-experience.md section 2
Layer 1 and section 9).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Final

from never4ga.schema.types import type_spec
from never4ga.services import dashboard
from never4ga.services.startup_command import DOCUMENTED_STARTUP_COMMAND

__all__ = [
    "AREA_INDEX",
    "BASES",
    "DOMAIN_REGISTRY_BODY",
    "HOME_BODY",
    "INBOX_SCRATCHPAD",
    "OBSIDIAN_README",
    "ROOT_INDEX",
    "ROOT_INDEXES",
    "SYSTEM_MANIFEST_BODY",
    "TEMPLATES",
    "WORKSPACE_INDEX",
    "skill_body_hash",
    "template_body",
    "template_for",
    "workspace_body",
]

#: The OKF bundle entry point (core/02 section 4). Written as a literal rather
#: than rendered: it is the one file whose exact two-line frontmatter the
#: specification prints verbatim, and the services layer owns no YAML codec.
ROOT_INDEX: Final = """---
okf_version: "0.2"
---
# Vault

The seven roots below are stable. Extend inside them rather than adding new
top-level folders.

- [Home](home.md) - Human-facing dashboard.
- [00_Inbox](00_Inbox/) - Unprocessed capture.
- [10_Workspaces](10_Workspaces/) - Bounded contexts actively pursued.
- [20_Life](20_Life/) - Ongoing areas of responsibility.
- [30_Knowledge](30_Knowledge/) - Durable reusable knowledge.
- [50_System](50_System/) - Standards, schemas, templates, integrations.
- [90_Archive](90_Archive/) - Intentionally detached historical material.
"""

HOME_BODY: Final = """# Home

## Where things go

| Question | Destination |
|---|---|
| Unprocessed? | `00_Inbox/` |
| Bounded active context? | `10_Workspaces/` |
| Ongoing area of life? | `20_Life/` |
| Durable reusable knowledge? | `30_Knowledge/Notes/` |
| Curated knowledge navigation? | `30_Knowledge/Maps/` |
| Governs the system or workflows? | `50_System/` |
| Detached historical material? | `90_Archive/` |

## Active workspaces

Workspaces live in [10_Workspaces](10_Workspaces/). Each has a
`workspace.md` describing what it is and where it stands.

## Life areas

Long-lived responsibilities live in [20_Life](20_Life/), each with an
`area.md`.

## Recent knowledge

Durable notes live in [30_Knowledge/Notes](30_Knowledge/Notes/) and stay
semantically flat. Classification uses type, domains, tags, links and
[Maps](30_Knowledge/Maps/) rather than folders.

## System

[50_System](50_System/) holds standards, schemas, templates and integration
records. The vault's own identity is [system.md](50_System/system.md).
"""

DOMAIN_REGISTRY_BODY: Final = """# Domain Registry

The controlled domain vocabulary (core/02 section 9.1). A domain answers *which
broad area of knowledge or responsibility is this relevant to* -- never what a
document is exactly about. Exact subjects are tags, links and Entity records.

To register a domain, add it to `values` in this file's frontmatter. Unknown
domains are a strict-validation warning until registered, so a new value can be
used first and promoted once it earns its place. Keep the list broad, stable
and short: if a value only fits one project, it is a tag.
"""

SYSTEM_MANIFEST_BODY: Final = """# System

This document's `id` is the stable identity of this vault. It does not change
when the vault directory is moved or renamed.

## Canonical store

Markdown with YAML frontmatter is the only canonical store. Indexes, caches and
projections are derived and rebuildable, and the vault remains useful with no
tooling running.

## Schema

Concepts follow `never4ga/0.1`. Every concept carries `type`, `id`, `schema`,
`title` and `created_at`. Unknown fields, types and profiles are preserved.

## Reserved names

`index.md` is navigation. `log.md` is directory history. Semantic manifests are
`workspace.md`, `area.md` and this file.
"""

#: The Inbox scratchpad, created by `init` so it is simply *there* the first
#: time somebody opens the vault. A verb that creates its own file on first use
#: is fine for an agent and useless to a person in Obsidian, who has to be told
#: the file exists before they can look for it.
#:
#: No managed markers, deliberately: every top-level bullet in the file is a
#: thought, and the prose has none. A bullet typed outside a fenced region and
#: silently ignored is the exact trap a scratchpad must not have.
INBOX_SCRATCHPAD: Final = """# Scratchpad

Somewhere to jot a thought down quickly so it is not forgotten. One per line,
as a bullet. Type straight into this file, or run
`never4ga scratch add "..."` from a terminal.

Every session reads these at startup and files them. Lines leave the way Inbox
items leave: they become something, or they are discarded with a reason.
Nothing is deleted for being untidy.
"""

#: Navigation for each root. Reserved OKF progressive disclosure: links to
#: useful first-hop material and nothing else (core/01 section 4).
ROOT_INDEXES: Final = {
    "00_Inbox": """# Inbox

Unprocessed capture. Keep it shallow and empty it often.

Move items to a workspace, a life area or `30_Knowledge/Notes/` once you know
what they are.
""",
    "10_Workspaces": """# Workspaces

Bounded contexts that are actively managed or pursued.

Each workspace directory holds a `workspace.md` manifest. Child workspaces live
under a parent's `Workspaces/`. Source repositories stay outside the vault.
""",
    "20_Life": """# Life

Long-lived areas of responsibility.

Each area directory holds an `area.md` manifest. Bounded projects belonging to
an area still live in `10_Workspaces/` and are linked from here.
""",
    "30_Knowledge": """# Knowledge

- [Notes](Notes/) - Durable reusable knowledge. Semantically flat.
- [Maps](Maps/) - Curated navigation and synthesis.
- [Assets](Assets/) - Supporting binaries.

Notes are classified with type, domains, tags, links and typed relations, never
with a topical folder tree.
""",
    "50_System": """# System

- [system.md](system.md) - This vault's manifest and stable identity.
- [Standards](Standards/) - How work is done here.
- [Schemas](Schemas/) - Schema documentation and controlled vocabularies.
- [Templates](Templates/) - Starting points for new concepts.
- [Skills](Skills/) - Portable procedures.
- [Agents](Agents/) - Agent configuration.
- [Documentation](Documentation/) - Documentation of the system itself.
- [Integrations](Integrations/) - Integration records and tool-owned files.
""",
    "90_Archive": """# Archive

Intentionally detached historical material.

Archiving is a deliberate act. Completing a workspace does not move it here;
lifecycle metadata records completion. Subfolders mirror the live roots and are
created only when used.
""",
    "30_Knowledge/Notes": """# Notes

Durable reusable knowledge, kept flat.
""",
    "30_Knowledge/Maps": """# Maps

Curated navigation over the notes.
""",
}

WORKSPACE_INDEX: Final = """# {title}

- [Workspace](workspace.md) - Canonical overview and current state.
- [Context](Context/) - Current orientation and constraints.
- [Goals](Goals/) - Desired outcomes.
- [Plans](Plans/) - Active and historical plans.
- [Tasks](Tasks/) - Work items held in the vault.
- [Decisions](Decisions/) - Durable decisions.
- [Research](Research/) - Workspace-specific investigations.
- [Resources](Resources/) - Supporting material.
- [Logs](Logs/) - Activity and session history.
- [Child Workspaces](Workspaces/) - Nested bounded contexts.
"""

AREA_INDEX: Final = """# {title}

- [Area](area.md) - What this area covers and how it stands.

Bounded projects belonging to this area live in `10_Workspaces/`.
"""


def workspace_body(title: str) -> str:
    """The `workspace.md` body -- the workspace shape, and nothing else.

    Kept as a name because callers use it; it is the same string as
    ``template_body("workspace", title)`` and can never drift from the template.
    """
    return template_body("workspace", title)


@dataclass(frozen=True, slots=True)
class _Shape:
    """One concept type's shape: the body a document of that type starts from.

    ``sections`` are the level-two headings of the body, in order; ``notes``
    are one-line prompts that render under each so a person opening the file
    knows what the section is for and deletes the prompt as they write. A
    creation verb renders the same body with the prompts, and the same
    headings, so the file a person starts by hand and the file `never4ga`
    writes are the same file.

    There are no frontmatter placeholders. A person cannot fill them -- `id`
    must be a UUIDv7 and `created_at` a canonical timestamp -- and a
    half-filled block turns prose into a `missing_id` error. The hand-written
    path runs the other way: write the body, then `adopt`, and Never4gA writes
    the frontmatter only it can write.
    """

    sections: tuple[tuple[str, str], ...]
    #: Extra body content that is not a heading -- the workspace dashboard's
    #: embedded Bases. Keyed by the section it follows.
    embeds: Mapping[str, str] = field(default_factory=dict)
    #: Type-specific guidance rendered in the *template* only, under the
    #: standing adopt note -- what to do after adopting that the section
    #: prompts cannot say, like which frontmatter fields the type carries.
    template_note: str = ""

    def body(self, title: str, *, live: bool = True) -> str:
        """Render the body.

        ``live`` includes the embedded views. A *template* is rendered without
        them: core/01 section 5 requires every shipped Markdown file to be
        readable with no plugin and to depend on no `.base` view, and a
        template is a file a person reads before anything is live. The
        creation verb renders with them, because a created workspace is
        meant to be looked at in Obsidian, and the section headings still
        read correctly anywhere else.
        """
        parts = [f"# {title}\n"]
        for heading, note in self.sections:
            parts.append(f"\n## {heading}\n")
            if note:
                parts.append(f"\n{note}\n")
            embed = self.embeds.get(heading) if live else None
            if embed:
                parts.append(f"\n{embed}")
        return "".join(parts)


#: The shape of each concept type. The reasoning for a shape sits beside it.
_SHAPES: Final[dict[str, _Shape]] = {
    # "Consequences" is plural because a decision rarely has only one.
    # "Alternatives" is present by default, because a decision without
    # alternatives is an announcement. "Revisit when" is what makes a decision
    # revisitable rather than permanent. Supersession is not a heading, because core/02
    # section 21.11 already makes it frontmatter: relations: superseded_by.
    "decision": _Shape(
        (
            ("Context", "What situation forced a choice, and what constrains it."),
            ("Decision", "What was decided, stated so it could be acted on without the rest."),
            (
                "Alternatives",
                "What else was possible and why it lost. Empty means it was not a decision.",
            ),
            ("Consequences", "What becomes easier, harder, or newly true."),
            ("Revisit when", "The condition under which this should be reopened."),
        ),
    ),
    # "Decided" and "Open" name what changes hands between sessions, so a log
    # can be read as the handoff it is (core/08: episodic memory lives here).
    # "Corrected" holds the claim that outran what had been checked, which is
    # the most reusable lesson a log carries.
    "activity_log": _Shape(
        (
            (
                "What happened",
                "In order, with times where they matter. What was tried, not only what worked.",
            ),
            ("Decided", "Rulings taken during this work, so they are not lost in the narrative."),
            ("Corrected", "What was wrong and got fixed, so the next session does not repeat it."),
            ("Open", "What was left unresolved, and who it waits on."),
            ("Next", "The first thing the next session should do, so it does not re-derive it."),
        ),
    ),
    # "Done when" comes before the steps so the end is stated before the
    # means. "Not in scope" says what the plan deliberately leaves alone.
    # A plan is also a project's roadmap: "Phases" names each phase, and
    # each phase links the walkthrough that records how it was done
    # (core/02 sections 21.9 and 21.27).
    "plan": _Shape(
        template_note=(
            "A plan that carries out a goal says so with `relations: implements` naming the goal. "
            'Start a phase with `never4ga walkthrough start <plan> "<phase>"`, which creates its '
            "walkthrough and prints the line to add under the phase."
        ),
        sections=(
            ("Objective", "What this plan changes, in one paragraph a stranger could act on."),
            ("Done when", "The observable condition that closes this plan."),
            ("Steps", "Numbered, each one checkable, with what it depends on."),
            (
                "Phases",
                "For work done in phases: one `### N. Title` per phase, each with what it "
                "delivers, its done-when, and a link to its walkthrough once it starts.",
            ),
            ("Not in scope", "What this plan deliberately leaves to another."),
        ),
    ),
    # A walkthrough is read by somebody recreating or following the work, so
    # every step carries what was done, the files, the exact commands, why,
    # and how it was checked. "Decided before starting" holds the choices the
    # steps assume; "What is left" is what the next session picks up.
    "walkthrough": _Shape(
        template_note=(
            "Link the plan with `relations: implements` naming it, set `phase` to the phase as "
            "the plan names it, and keep `lifecycle` current: `in_progress` while the phase "
            "runs, `complete` when its done-when is met. Declare each step you write with "
            '`never4ga checkpoint --walkthrough "<ref>: step N"`.'
        ),
        sections=(
            ("Decided before starting", "The choices the steps below assume, and who made them."),
            (
                "Steps",
                "One `### N. <step>` per step, in the order it was done, each with:\n\n"
                "**What:** what this step changed.\n\n"
                "**Files:** the files it created or edited.\n\n"
                "**Commands:** the exact commands, in a fenced block.\n\n"
                "**Why:** the reason for doing it this way.\n\n"
                "**Checked by:** how it was verified, as something observable.",
            ),
            ("What is left", "What remains of this phase, so the next session starts there."),
        ),
    ),
    # A runbook is read by somebody who is not calm: the thing is down, or
    # about to be. So the shape front-loads recognition and access -- am I in
    # the right document, and can I even run this -- before the first command,
    # and it ends with the two things a procedure is worthless without: how
    # you know it worked, and what to do when it did not. `Verify` is not
    # optional politeness; a runbook whose last step is an action leaves the
    # reader guessing whether they are finished.
    "runbook": _Shape(
        (
            (
                "When to use this",
                "The symptom or trigger, written so somebody can recognise it in a hurry.",
            ),
            ("Before you start", "Access, tools and state this assumes. Where to get each."),
            ("Steps", "Numbered, in order, with the exact commands. One action per step."),
            ("Verify", "How you know it worked, as something observable rather than assumed."),
            ("If it goes wrong", "The failure this procedure can cause, and how to get back."),
        ),
    ),
    "goal": _Shape(
        (
            ("Outcome", "What is true when this goal is met."),
            ("Why now", "What makes this the moment, and what it costs to wait."),
            ("Measured by", "How anyone would know."),
        ),
    ),
    "task": _Shape(
        (
            (
                "Done when",
                "The observable condition that closes it. Empty means nobody can close it.",
            ),
        ),
    ),
    "research_note": _Shape(
        (
            ("Question", "The one question this note exists to answer, as asked."),
            ("Findings", "What was found, dated, with how sure it is."),
            (
                "Sources",
                "Where each finding came from, so it can be rechecked. Durable ones go in "
                "`sources` in the frontmatter.",
            ),
            ("What this changes", "Which decision or plan should hear about it."),
        ),
    ),
    "course_assignment": _Shape(
        (
            ("Prompt", "What is being asked, as the course states it."),
            ("Rubric", "How it is graded. Empty means the course gave none."),
            ("Notes", "Working notes, drafts and dead ends. Only the submission has to be tidy."),
            (
                "Submission",
                "What was turned in, where it lives, and what it scored.",
            ),
        ),
    ),
    "course_unit": _Shape(
        (
            ("Graded this unit", "What is due, and when. Empty means reading only."),
            ("Reading", "Each assigned text, and what it was for."),
            ("Videos", "Each one, and the one thing it added that the reading did not."),
            (
                "Notes",
                "The unit in my own words. If it only repeats the reading, it is not a note.",
            ),
            ("Questions", "Things I did not follow, to bring back or ask about."),
            (
                "Worth keeping",
                "Anything that outlives the term is promoted to a knowledge note "
                "and linked back from here.",
            ),
        ),
    ),
    "context": _Shape(
        (
            ("Orientation", "What someone arriving needs to know first."),
            ("Constraints", "What binds work here that is not obvious from the code or the plan."),
            (
                "Known limitations",
                "What does not work yet, and is known not to, so nobody rediscovers it.",
            ),
        ),
    ),
    # A dismissal is a judgement, and a judgement with no reasoning is a
    # preference. "What the finding said" keeps the rule's own words next to
    # the reason for overruling them, so a reader can tell a wrong rule from a
    # right rule about a correct document.
    "finding_dismissal": _Shape(
        (
            (
                "What the finding said",
                "The rule's own words, so the reason is judged against them.",
            ),
            (
                "Why it is dismissed",
                "Whether the rule is wrong, or right about a document that is correct anyway.",
            ),
            ("What would make it valid again", "The change that should bring the finding back."),
        ),
    ),
    # A standard that says only what the rule is invites the question it exists
    # to settle. "Applies to" is the scope; "Rationale" is what stops it being
    # re-litigated.
    "standard": _Shape(
        template_note=(
            "Where a standard sits decides who it binds: `50_System/Standards/` binds the "
            "whole vault, a workspace's profile folder binds that workspace and its children."
        ),
        sections=(
            ("Rule", "The rule itself, stated so it can be followed without the rest."),
            (
                "Applies to",
                "Who and what it binds. Location decides the scope; `relations: applies_to` "
                "names a concept.",
            ),
            ("Rationale", "Why this and not the alternative, so it is not re-litigated."),
        ),
    ),
    # Resource records vary widely: a list of specification URLs, a scope
    # note, a long vendor manual. So the shape orients rather than prescribes.
    # "Currency" is the heading that earns its place -- a resource record
    # points at material that moves, and its usual failure is a reader
    # trusting a version string nobody rechecked. core/02 section 21.13's
    # `resource:` field is in the frontmatter, where a URI belongs, not in a
    # heading.
    "resource": _Shape(
        template_note=(
            "A resource that points at something outside the vault carries `resource`, "
            "the URI or path it points at."
        ),
        sections=(
            ("Purpose", "What the material is, and who needs it."),
            ("Contents", "What is in it, enough to decide whether to open it."),
            ("Related material", "What else covers this, and where the boundary sits."),
            ("Currency", "When this was last checked against the source, and what would age it."),
        ),
    ),
    # Documentation is often the specification of a product, in a workspace's
    # Architecture/ or in 50_System/Documentation/. "Boundaries" is there
    # because documentation's characteristic failure is a reader assuming it
    # covers what it never claimed to.
    "documentation": _Shape(
        (
            ("Purpose", "What this documents, and who needs it."),
            ("How it works", "The mechanism, at the depth a maintainer needs and a user does not."),
            (
                "Boundaries",
                "What this deliberately does not cover, so a reader does not assume it does.",
            ),
            ("Currency", "What would make this wrong, and when it was last checked."),
        ),
    ),
    # core/02 section 21.17. `domain-registry.md`, which `init` already writes,
    # is the model: it says what question a value answers, says plainly what it
    # never answers, and states the bar for adding one. The values themselves
    # are frontmatter -- a registry whose vocabulary lived in prose would be a
    # controlled vocabulary nothing could read.
    "registry": _Shape(
        (
            ("The vocabulary", "What question a value answers -- and what it never answers."),
            (
                "Registering a value",
                "The bar a candidate clears before it joins `values` in this file's frontmatter.",
            ),
        ),
    ),
    # core/02 section 21.16, "human-readable/schema-governance description".
    # Governance is the operative word, so "Compatibility" is a section rather
    # than a sentence: what may change without a version bump is the question a
    # schema document exists to answer, and core/02 section 5.1's tolerance
    # rules are exactly such an answer.
    "schema_definition": _Shape(
        (
            ("What this governs", "Which documents or values this schema applies to."),
            ("Fields", "Each field, whether it is required, and what values it takes."),
            ("Rules", "What a validator checks, and at which level."),
            (
                "Compatibility",
                "What may change without a version bump, and what may not.",
            ),
        ),
    ),
    # core/02 section 21.19. Two rules from elsewhere shape this one. Tool-owned
    # files under `Integrations/<Name>/` are foreign-format and are not
    # concepts, so "What it owns" has to distinguish them; and core/05 section
    # 19 makes every integration optional, so an integration document that
    # never says what happens without it has left out the part that matters
    # when the external system is down.
    "integration": _Shape(
        # The four connection fields are the canonical half of core/03 section
        # 16's split, and `provider`/`project_ref` are spelled as
        # details/openproject-adapter.md section 4 spells them on a workspace,
        # so a reader meets one vocabulary rather than two. They ride in the
        # template's note because templates are body-only. There is
        # deliberately no mention of a field for the token: the secret store
        # holds it, and `doctor` reports it as an error if one appears anyway.
        template_note=(
            "A connection definition then carries four properties: "
            "`connection`, `provider`, `base_url`, `project_ref`. The secret "
            "itself never enters the vault: the secret store holds it, keyed "
            "by the connection name."
        ),
        sections=(
            ("What it integrates", "The external system, and what Never4gA does with it."),
            (
                "What it owns",
                "The files and settings this integration manages. Tool-owned files under "
                "`Integrations/<Name>/` are foreign-format and are not concepts.",
            ),
            (
                "Credentials",
                "Where they live. Never in the vault (core/05 section 17): the secret "
                "store holds the value, keyed by connection name, and this document "
                "carries only the name.",
            ),
            (
                "When it is unavailable",
                "What still works without it. Nothing may depend on it being reachable.",
            ),
        ),
    ),
    "knowledge": _Shape(
        (),
        template_note=(
            "A knowledge note carries `description`, one line a search result can show; "
            "`domains`, from the domain registry; `sources`, where it came from; and "
            "`verified` with `stale_after`, when it was last checked and when to doubt it."
        ),
    ),
    "map": _Shape(
        (
            ("Start here", "The few links a newcomer should follow first, in order."),
            ("Related maps", "The maps beside this one, and what each covers that this does not."),
        ),
    ),
    # core/02 section 21.23. Headings rather than fields: `20_Life/Relationships/` is "a
    # lightweight personal record -- not a CRM", so the shape asks who somebody
    # is and how you know them, and stops there. Anything more structured would
    # be the CRM the area says it is not.
    "person": _Shape(
        (
            ("Who they are", "What they do and where, enough to place them."),
            (
                "How we know each other",
                "The context, and anything worth remembering before the next conversation.",
            ),
        ),
        template_note=(
            "A person carries `aliases` for the other names they go by, lives only "
            "in a life area, and never carries `workspace`."
        ),
    ),
    "life_area": _Shape(
        (
            (
                "Responsibilities",
                "What this area is answerable for, so a document knows whether it belongs here.",
            ),
            (
                "Current State",
                "Where things stand, in a paragraph that is rewritten rather than appended to.",
            ),
        ),
    ),
    # details/obsidian-experience.md section 5: workspace.md is both manifest
    # and dashboard, and Bases may be embedded for live vault-derived sections.
    # The views themselves live in the managed block `services.dashboard`
    # owns, one per kind of content the workspace actually holds. This shape
    # carries the block's empty starting state, and the refresh every creating
    # verb runs grows it as content arrives. Views baked into the body could
    # never gain a new one, because nothing re-renders a body once written.
    "workspace": _Shape(
        (
            ("Purpose", "What this workspace exists to produce, and for whom."),
            (
                "Current State",
                "Where it stands, rewritten as it changes. The views below are generated.",
            ),
            (
                "External Systems",
                "Repositories: see `repositories` in this file's frontmatter, and "
                "`never4ga workspace mappings` for where each sits on this machine. "
                "Work management: see `work_management`, when declared.",
            ),
            ("Key Links", "- [Context](Context/)\n- [Logs](Logs/)"),
        ),
        embeds={
            # Under Current State: the block sits
            # between the manifest prose and External Systems, and a person
            # may move the markers -- the splice follows them, wherever they
            # are, and owns only what is between them.
            "Current State": dashboard.block_for(frozenset()) + "\n",
        },
        template_note=(
            "The dashboard views -- Goals, Plans, Units and the rest -- are "
            "generated per kind of content once the workspace is tracked; any "
            "creation refreshes them, and `never4ga repair --apply` fills "
            "them in everywhere."
        ),
    ),
}


def _template_filename(type_name: str) -> str:
    """File names are lowercase, and hyphens join the words of one idea.

    The template *file* is `life-area.md` while the type *value* stays
    `life_area`; those are different namespaces.
    """
    return type_name.replace("_", "-") + ".md"


def template_body(type_name: str, title: str) -> str:
    """The body a creation verb writes for this type. Raises KeyError if unshaped."""
    return _SHAPES[type_name].body(title)


def shaped_types() -> frozenset[str]:
    """The types that have a body shape.

    Three registered types do not -- `dashboard`, `system_manifest` and
    `skill_provenance` -- because Never4gA writes each of them and nobody
    starts one by hand. A creation verb falls back to the title alone for
    those, rather than refusing to create a registered type.
    """
    return frozenset(_SHAPES)


def fields_note(type_name: str) -> str:
    """What the frontmatter of this type carries, read from the registry.

    A template can say what to write under each heading and still leave an
    author to guess the lifecycle vocabulary, the due-date field, or which
    authority a type recommends -- and templates are body-only by design, so
    none of it can sit in a YAML placeholder.
    It sits in the adopt note instead, and it is *generated* from `core/02`
    section 21's registry rather than typed into twenty-two files, so a
    vocabulary has one home and a template cannot drift from it. Empty when the
    type carries nothing beyond what every concept does.

    `workspace` and `area` are left out: `adopt` writes the scope from where
    the file sits, and telling an author to set it would be telling them to
    do the one thing the verb exists to do for them.
    """
    spec = type_spec(type_name)
    if spec is None:
        return ""
    clauses: list[str] = []
    required = [name for name in spec.required_fields if name not in ("workspace", "area")]
    for name in required:
        if name == "lifecycle" and spec.lifecycle_values:
            clauses.append(f"`lifecycle`, one of {_listed(spec.lifecycle_values)}")
        else:
            clauses.append(f"`{name}`")
    optional = [name for name in spec.extra_fields if name not in required]
    if optional:
        clauses.append(f"and may carry {_listed(tuple(f'`{name}`' for name in optional))}")
    parts: list[str] = []
    if clauses:
        parts.append(f"This type carries {', '.join(clauses)}.")
    if spec.recommended_authority:
        # Written, not recommended: `adopt` and `create` both go through
        # `build_concept`, which sets it, so the honest thing to tell an
        # author is what they will get unless they say otherwise.
        parts.append(
            f"`authority` is written as `{spec.recommended_authority}` "
            "unless a field says otherwise."
        )
    if not parts:
        return ""
    parts.append(
        f"Set a field at adoption with `--field <name>=<value>`; "
        f"`never4ga concept types {type_name}` lists them."
    )
    return " ".join(parts)


def _listed(values: tuple[str, ...]) -> str:
    """`a`, `b` or `c` -- a vocabulary as a sentence reads it."""
    quoted = [value if value.startswith("`") else f"`{value}`" for value in values]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def template_for(type_name: str) -> str:
    """The template file for this type: the body, and how it becomes a concept.

    Deliberately no frontmatter, not even a `type:` line -- a file that opens
    a block without an `id` is a `missing_id` error, so a template that began
    one would turn every note started from it into a finding until adopted.
    Body-only Markdown is legitimate prose from the first keystroke, surfaced
    gently as `untracked_document`, and `adopt` finishes the job.
    """
    shape = _SHAPES[type_name]
    note = (
        "> When the note is written, adopt it: `never4ga adopt <this file>`, or "
        "the companion's *Adopt this note*. Never4gA adds the frontmatter a "
        "person cannot write -- the id, the timestamp, the scope -- and the "
        "file stays exactly here."
    )
    # What the registry says the type carries, then what the shape knows
    # that the registry cannot express -- in that order, because the first is
    # the same sentence for every type and the second is the exception.
    fields = fields_note(type_name)
    if fields:
        note += f"\n>\n> {fields}"
    if shape.template_note:
        note += f"\n>\n> {shape.template_note}"
    return f"{note}\n\n{shape.body('<title>', live=False)}"


#: Starting points under `50_System/Templates/`. These are foreign-format files
#: (core/02 section 3.3): the placeholders are not valid concept values, and
#: Never4gA never indexes or validates them as concepts.
TEMPLATES: Final = {_template_filename(name): template_for(name) for name in _SHAPES}

#: The starting point for an authored Skill.
#:
#: It governs Skills written with `skills new`, and nothing else. A third-party
#: or vendored Skill arrives in whatever shape its author chose and keeps it;
#: nothing in `doctor` or `validate` treats conformance to this as a
#: requirement. It is a generator, not a gate: the missing `tree_digest` on a
#: freehand-authored Skill is what this exists to make impossible, by having
#: `skills new` write the provenance record in the same act.
#:
#: Seeded into `50_System/Templates/skill.md` beside the concept skeletons and
#: read back from there, so an edit to the vault copy is honoured the way an
#: edited template is (core/04 section 20). Unlike those skeletons it opens a
#: frontmatter block, because a `SKILL.md` is a foreign-format island whose
#: frontmatter is the Agent Skills contract rather than concept fields
#: (section 20), and a copy of it is never a `missing_id` finding.
#:
#: `<name>` and `<description>` are filled in by `skills new`; every other
#: angle-bracketed line is the author's to replace.
SKILL_TEMPLATE_FILENAME: Final = "skill.md"

SKILL_TEMPLATE: Final = """---
name: <name>
description: "<description>"
metadata:
  never4ga-source: "authored"
  never4ga-version: "0.1.0"
  never4ga-review-status: "draft"
  never4ga-exposure: "global"
---

# <name>

<One paragraph on what this Skill is for, in the words an agent will match a
task against. The description above is what a client lists; this is what the
agent reads once it has chosen.>

## When to use it

<The situation that calls for it, and the one that looks like it but does not.>

## Do this

1. <Each step a thing an agent can run or check, not a thing to bear in mind.>

## Never

- <What this Skill must not do, however the task is phrased.>

## Done when

<How the agent can tell it worked, without asking.>
"""


def authored_skill(name: str, description: str, *, template: str = SKILL_TEMPLATE) -> str:
    """One authored Skill, started from the template.

    Only the two placeholders `skills new` was given are filled. The
    description is escaped the way the shipped Skills escape theirs: a
    description is a sentence, sentences contain colons, and `key: value:
    value` is not YAML.
    """
    escaped = description.replace("\\", "\\\\").replace('"', '\\"')
    return template.replace("<description>", escaped).replace("<name>", name)


#: Everything `init` seeds under `50_System/Templates/` and `skills refresh`
#: keeps current: the concept skeletons, and the one template that is not a
#: concept.
SHIPPED_TEMPLATES: Final = {**TEMPLATES, SKILL_TEMPLATE_FILENAME: SKILL_TEMPLATE}

OBSIDIAN_README: Final = """# Obsidian

Obsidian is a first-class way to work with this vault, and entirely optional.
The vault is plain Markdown; nothing here is required to read it.

## Recommended settings

- **Files & Links -> Use [[Wikilinks]]: off.** Never4gA prefers standard
  Markdown links so the vault stays portable.
- **Files & Links -> New link format: relative path to file.**
- **Templates -> Template folder location: `50_System/Templates`.**

## Bases

`Bases/` holds saved views over frontmatter. They derive everything they show
from the Markdown properties already in the vault and store no canonical data of
their own. Deleting them loses nothing.

## What Obsidian must not become

Bases, plugin state and workspace layout are never the only representation of a
durable fact. If something matters, it lives in a concept's frontmatter or body.
"""

#: Obsidian Bases views (details/obsidian-experience.md section 4). YAML, read
#: by Obsidian and owned by it -- Never4gA writes these once and then leaves
#: them alone.
BASES: Final = {
    "active-workspaces.base": """# Every workspace still being pursued.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Workspace
  note.workspace_type:
    displayName: Kind
  note.description:
    displayName: What it is
filters:
  and:
    - note.type == "workspace"
    - note.lifecycle == "active"
views:
  - type: cards
    name: Active
    order:
      - formula.doc
      - note.description
      - note.workspace_type
    sort:
      - property: note.title
        direction: ASC
    image: note.cover
    imageFit: cover
    imageAspectRatio: 0.5
    cardSize: 280
  - type: table
    name: Table
    order:
      - formula.doc
      - note.workspace_type
      - note.lifecycle
      - note.description
    sort:
      - property: note.title
        direction: ASC
""",
    "life-areas.base": """# Long-lived areas of responsibility.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Area
  note.description:
    displayName: What it covers
filters:
  and:
    - note.type == "life_area"
views:
  - type: cards
    name: Areas
    order:
      - formula.doc
      - note.description
    sort:
      - property: note.title
        direction: ASC
    cardSize: 240
  - type: table
    name: Table
    order:
      - formula.doc
      - note.lifecycle
      - note.description
""",
    "knowledge.base": """# Durable notes. The folder is flat, so these views are the navigation.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Note
  note.description:
    displayName: What it says
filters:
  and:
    - note.type == "knowledge"
views:
  - type: table
    name: By domain
    groupBy:
      property: note.domains
      direction: ASC
    order:
      - formula.doc
      - note.description
      - note.tags
    sort:
      - property: note.title
        direction: ASC
  - type: table
    name: Recently touched
    limit: 30
    order:
      - formula.doc
      - note.domains
      - file.mtime
    sort:
      - property: file.mtime
        direction: DESC
  - type: table
    name: All
    order:
      - formula.doc
      - note.description
      - note.domains
      - note.tags
      - note.status
""",
    "people.base": """# The people this vault knows about.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Person
filters:
  and:
    - note.type == "person"
views:
  - type: table
    name: All
    order:
      - formula.doc
      - note.description
      - note.aliases
    sort:
      - property: note.title
        direction: ASC
""",
    "goals.base": """# Desired outcomes, across every workspace.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Goal
filters:
  and:
    - note.type == "goal"
views:
  - type: table
    name: Open
    filters:
      and:
        - note.lifecycle != "achieved"
        - note.lifecycle != "abandoned"
    order:
      - formula.doc
      - note.lifecycle
      - note.target_date
      - note.workspace
    sort:
      - property: note.target_date
        direction: ASC
  - type: table
    name: All
    order:
      - formula.doc
      - note.lifecycle
      - note.target_date
      - note.workspace
""",
    "plans.base": """# Plans, across every workspace.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Plan
filters:
  and:
    - note.type == "plan"
views:
  - type: table
    name: Active
    filters:
      and:
        - note.lifecycle == "active"
    order:
      - formula.doc
      - note.workspace
      - file.mtime
    sort:
      - property: file.mtime
        direction: DESC
  - type: table
    name: All
    groupBy:
      property: note.lifecycle
      direction: ASC
    order:
      - formula.doc
      - note.lifecycle
      - note.workspace
""",
    "decisions.base": """# What has been settled. The title is the sentence a decision settles,
# which is why these views lead with it rather than with the file name.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Decision
filters:
  and:
    - note.type == "decision"
views:
  - type: table
    name: In force
    filters:
      and:
        - note.lifecycle == "accepted"
    order:
      - formula.doc
      - note.workspace
      - file.mtime
    sort:
      - property: file.mtime
        direction: DESC
  - type: table
    name: By workspace
    groupBy:
      property: note.workspace
      direction: ASC
    order:
      - formula.doc
      - note.lifecycle
      - note.authority
  - type: table
    name: Superseded
    filters:
      and:
        - note.lifecycle == "superseded"
    order:
      - formula.doc
      - note.workspace
""",
    "recent-activity.base": """# Episodic history. Every log carries occurred_at,
# so a day is a query rather than a document.
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Log
  note.occurred_at:
    displayName: When
filters:
  and:
    - note.type == "activity_log"
views:
  - type: table
    name: Recent Activity
    limit: 30
    order:
      - formula.doc
      - note.occurred_at
      - note.workspace
    sort:
      - property: note.occurred_at
        direction: DESC
  - type: table
    name: By workspace
    groupBy:
      property: note.workspace
      direction: ASC
    order:
      - formula.doc
      - note.occurred_at
    sort:
      - property: note.occurred_at
        direction: DESC
""",
    "stale-concepts.base": """# Anything that has outlived the date it set
# for itself (core/02 section 15).
formulas:
  doc: 'file.asLink(if(note.title, note.title, file.name.replace("-", " ").title()))'
properties:
  formula.doc:
    displayName: Concept
  note.stale_after:
    displayName: Stale since
filters:
  and:
    - note.stale_after != null
    - note.stale_after < now()
views:
  - type: table
    name: Stale Concepts
    order:
      - formula.doc
      - note.type
      - note.stale_after
      - note.status
    sort:
      - property: note.stale_after
        direction: ASC
""",
}


def skill_body_hash(body: str) -> str:
    """The fingerprint a seeded Skill carries so an edit is a fact, not a memory.

    `core/04` section 25 makes the vault-to-client hop decidable with a
    machine-local ownership manifest. The shipped-to-vault hop cannot use one:
    the vault is the thing that travels between machines, and a manifest on one
    machine says nothing about a vault cloned onto another. So the provenance
    lives in the file, and "did the user edit this?" is answered by rehashing
    the body rather than by remembering having written it.

    The body only. Frontmatter carries the hash itself and would have to be
    excluded from its own input; it also carries `description`, which a user may
    reasonably tune without meaning "never update this again".
    """
    return sha256(body.encode()).hexdigest()


def _skill(
    name: str,
    description: str,
    body: str,
    *,
    exposure: str = "bootstrap",
    version: str,
) -> str:
    """One canonical Skill, in the open Agent Skills format.

    `core/04` section 20 makes `SKILL.md` a declared foreign-format island:
    Never4gA MUST NOT force concept frontmatter onto it. So the frontmatter here
    is the Agent Skills contract -- `name` and `description` -- plus section 21's
    `metadata`, whose values are strings so the file stays valid to any client
    that reads the standard.

    `version` is required rather than defaulted. Section 21 prints the field but
    never says who moves it. A default would stay put through every edit, and a
    version that never changes is worse than none: it reads like a promise the
    text has not kept.
    """
    # Quoted, always. A description is a sentence, sentences contain colons,
    # and `key: value: value` is not YAML -- a client would fail to parse the
    # Skill rather than merely misread it.
    escaped = description.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{escaped}"\n'
        "metadata:\n"
        '  never4ga-source: "authored"\n'
        f'  never4ga-version: "{version}"\n'
        '  never4ga-review-status: "reviewed"\n'
        f'  never4ga-exposure: "{exposure}"\n'
        f'  never4ga-seed-hash: "{skill_body_hash(body)}"\n'
        "---\n"
        f"{body}"
    )


#: The eight canonical Skills, fixed by `core/04` sections 22 and 32.
#:
#: Every one of them tells the agent to run a CLI verb rather than describing
#: what the verb does. `core/04` section 16 makes the CLI the universal
#: bootstrap fallback, and a Skill that restated a service's behaviour would be
#: a second place for that behaviour to be wrong.
SKILLS: Final[dict[str, str]] = {
    "never4ga-startup": _skill(
        "never4ga-startup",
        "Opens a session with the smallest useful context: resolves the current "
        "repository to its workspace and loads a bounded startup Context Pack. Use "
        "at the beginning of any substantive session, before other work.",
        """
# Start a session

Run this before substantive work, once.

```bash
"""
        + DOCUMENTED_STARTUP_COMMAND
        + """
```

**Name yourself in `--actor`.** `core/02` section 5.2 makes it a MUST: anything
you write records you in `generated.by`, and the format is
`<producer>/<version>` -- `claude-code/claude-opus-5`, not `claude-code`.
Never4gA cannot work it out, because the process looks the same whoever started
it, and without it every document you create claims a person wrote it.

Pass it once here and the session carries it: `never4ga concept create ...
--session <id>` then records you without repeating the flag.

Add `--task "<short description>"` when you already know what the session is
for. Keep it short: it becomes retrieval terms, and the full prompt is never
persisted merely because retrieval used it.

## Reading the pack

Every item says why it is there. Read them in the order given -- the pack is
ordered by what a session needs first, not by relevance score:

- **workspace** and **parent** -- what this repository *is*, and what it belongs to
- **context** -- current state and constraints
- **standard** -- the rules that bind this work
- **decision** -- what was already settled, so it is not relitigated
- **activity** -- what the last session did and left open

The output is the whole pack: an index of the items first, then every body it
carries under that document's path.

**Required reading is not optional** (`core/07` §10). The workspace page, its
parents' pages, every document in the workspace's `Context/`, the newest
activity log and the standards that bind every session are marked
`[required]`, and they are what this workspace needs a session to know.
Those that fit are printed below the index. Each one that did not is marked
`[required, read in full: <path>, <size> characters]`. **Read every one of
those in full, with your own file-reading tool, before substantive work** --
page through it if it is long, and never stop at the first screen. Never4gA
never cuts them to fit, so nothing else will give you what they say.

If your shell saves long output to a file and shows you a preview, read that
file too: the preview is not the pack.

The `required reading:` line states the total against its ceiling. If it
carries a `[W]`, say so -- the workspace needs something moved out, and what
moves is the vault owner's call.

Anything else marked `[reference <id>]` is a pointer, not a body. Fetch one
with `never4ga concept get <id>` when it turns out to matter.

**A standard listed by reference still binds the work it describes.** The line
under it says what it governs. Before work that touches that -- a palette, the
books, a release -- fetch it and follow it.

**A walkthrough listed as the current phase is the record you add to.** It is
listed by reference because it is long. Before working on that phase, read
its `## What is left` and the last step, and add each step you complete to it
(`never4ga-checkpoint` says how). A phase about to start has none yet: create
it with `never4ga walkthrough start <plan> "<phase>"`.

**Do not run startup a second time to read the pack** -- not with `--json`, not
to see it again. Every startup opens a new session, and the second one is a
stray: empty, never wrapped, and in the way of the ones that are real. If you
need more, `never4ga concept get` fetches a document and `never4ga context
focus` retrieves inside the session you already have.

## Surface the Inbox, and stop there

The pack carries `inbox.pending` and `inbox.items` when captures are waiting.
**Show them at the start of the session, before other work — and file
nothing.**

```bash
never4ga inbox list
```

Read each one, say what it looks like to you, and say where you think it
belongs. Then wait for an answer. Do not create a work item, do not create a
concept, do not resolve anything, until you have been told to.

**This is not caution, it is the rule**, as it was first put: *the inbox is
meant to surface items that need to be filed, not to file them and tell me
about it later.* A capture is a thought the vault's owner kept without deciding
what it was. Deciding is theirs; surfacing it so they can decide is yours.

**Placement is usually not inferable, and trying harder does not help.** A
session filed a capture about ADR retrieval into the wrong project because it
read the mechanical cause as the subject. Nothing in the capture said which
project it belonged to — it was not a failure to infer well, it was a thing
that could not be inferred at all. That is why confirming is not optional and
not a matter of confidence.

What you may say about an item, once you have read it:

- it looks durable and reusable, so perhaps a `knowledge` note
- it looks like work, so perhaps a tracker item — **in which project?**
- it looks like a ruling, so perhaps a `decision`, and you never accept one
- it looks like workspace material, so perhaps that workspace's own folder

Each of those is a proposal with a question mark, not a plan.

## Once they have answered

Then, and only then, create what they asked for and account for the item:

```bash
never4ga inbox resolve <path> --into <concept-id>
never4ga inbox resolve <path> --work <item> --session <id>
```

**Nothing leaves the Inbox until something accounts for it, and once
something does, it leaves.** Never4gA checks rather than trusts: a concept
must exist in the vault, and a work item must have a write this session
actually recorded — saying you filed a ticket is a claim, and the session
store is the fact. Never `rm` an item; that is the unowned write `resolve`
exists to replace.

**And the destination has to resurface.** A concept that exists but reaches
no structural lane is not an accounting — filing a thought there swaps a
place that reminds you for a place that does not, which is the opposite of
what capturing it was for. `resolve` refuses those and says why; the item
stays, and the next session is asked about it again. Reference material is
unaffected: a `knowledge` note is reached by looking for it and was never
promised to arrive unasked.

**An item they do not answer about stays.** An Inbox that is still full at the
end of a session is a normal outcome, not a failure to tidy.

## The scratchpad

`00_Inbox/scratchpad.md` is where the vault's owner jots something down away
from a session, so they do not forget it -- a line, not a paragraph, written in
Obsidian or from a terminal. **It is theirs to write and yours to process.** You
have no reason to add a line: you are in a session with them, so anything worth
keeping gets filed where it belongs now, as a ticket, a concept or workspace
material. A scratch line you wrote and then processed is a note you passed to
yourself.

Its lines reach the pack as `inbox.scratch` and `inbox.scratch_items`, and
they are your business at startup exactly as the pile is. The file itself is
never listed as pending: it is drained, not processed, and it stays.

```bash
never4ga scratch list
never4ga scratch resolve <n> --into <concept-id>
never4ga scratch resolve <n> --work <item> --session <id>
never4ga scratch resolve <n> --discard --reason "<why>"
```

A line leaves under the same accounting a captured file leaves under. It is
smaller; it is not more expendable -- and ask before discarding one, for the
reason the pile has: you were not there when they wrote it.

An item that is genuinely not worth keeping is still accounted for:

```bash
never4ga inbox resolve <path> --discard --reason "<why>"
```

Ask before discarding anything you did not capture yourself. The pile is
somebody's thinking, and the reason you cannot see the value is sometimes
that you were not there.

## If it cannot resolve

The repository is not mapped to a workspace. Say so rather than guessing:

```bash
never4ga workspace resolve --path "$PWD"
never4ga workspace map <workspace-id> --repo "$PWD"
```
""",
        version="0.12.0",
    ),
    "never4ga-context": _skill(
        "never4ga-context",
        "Retrieves more context mid-session at a stated depth. Use when the "
        "startup pack was not enough, when work moves to an unfamiliar area, or "
        "when a broad investigation needs the whole workspace lineage.",
        """
# Get more context

Escalate progressively. Do not start here, and do not reach for `deep` first.

## Focused -- about particular terms

```bash
never4ga context focus "<terms>" --path "$PWD"
```

Lexical retrieval inside this workspace and its parents, expanded one hop
through typed relations. This is the usual answer.

## Deep -- a broad investigation

```bash
never4ga context startup --depth deep --path "$PWD"
never4ga context focus "<terms>" --depth deep --path "$PWD"
```

Wider: the lineage's own context and decisions, `30_Knowledge/` admitted, a
second relation hop, and more room. It stays inside the workspace and its
parents -- a sibling workspace is never reached by depth alone.

Deep packs are large. Use it when a question genuinely spans the lineage, not
because focused returned little.
""",
        version="0.2.0",
    ),
    "never4ga-capture": _skill(
        "never4ga-capture",
        "Puts a thought, link or fragment into the Inbox without deciding where "
        "it belongs. Use when something is worth keeping but classifying it now "
        "would interrupt the work.",
        """
# Capture it, decide later

```bash
never4ga capture "<the thought, in full>"
```

An Inbox item is deliberately not a concept. It has no type, because choosing
one is the act of processing it -- so nothing is being decided here, and that is
the point.

Capture when interrupting the work to classify would cost more than the
classification is worth. When you already know what the thing is and where it
belongs, use `never4ga-create` instead.

Never edit `00_Inbox/index.md`: it is reserved navigation.
""",
        version="0.2.0",
    ),
    "never4ga-create": _skill(
        "never4ga-create",
        "Creates a concept of any registered type in the right folder with valid "
        "frontmatter. Use when writing a decision, plan, note, log, standard or "
        "any other durable document into the vault.",
        """
# Create a concept

Never hand-write frontmatter. The verb mints the UUIDv7, stamps the timestamps,
works out the folder from the Type Registry and refuses anything that would not
validate.

```bash
never4ga concept create <type> "<title>" --workspace <id>
```

- `--in <folder>` when a type has several legal homes and Never4gA says so
- `--field name=value` for anything the type requires; repeat a name for a list
- `--description "<one sentence>"`
- `--session <id>` so `generated.by` records **you**

**Pass `--session`.** Without it the document says `human:owner` wrote it, which
is a false claim about authorship in a vault whose whole point is that its
claims are true. `core/02` section 5.2 makes naming yourself a MUST, and the
session already knows who you are because startup was told. `--actor` overrides
it when you need to say something more precise for one document.

The folder is inferred and the inference is reported. If Never4gA refuses, it
names the legal locations -- pick one rather than working around it.

## Which type

Ask what the document *is*, not where you want it:

| It is | Type |
|---|---|
| a ruling, with alternatives and consequences | `decision` |
| a plan of work with an end condition | `plan` |
| durable reusable knowledge, not project-specific | `knowledge` |
| a record of what happened in a session | `activity_log` |
| a rule that binds work | `standard` |
| supporting material, or a pointer to some | `resource` |
| an investigation with a question | `research_note` |

The two manifest types have their own verbs: `never4ga workspace create` and
`never4ga life-area create`.
""",
        version="0.3.0",
    ),
    "never4ga-doctor": _skill(
        "never4ga-doctor",
        "Checks vault health and explains what to do about each finding. Use "
        "before finishing a session that wrote to the vault, or when something "
        "looks wrong with indexing, links or frontmatter.",
        """
# Check the vault

```bash
never4ga doctor
never4ga validate            # frontmatter only, per document
```

`doctor` never changes the vault. It reports and stops -- repair is always an
explicit act. What it does write is derived: a finding is recorded in the
index's own database so it has a date, and so the run that fixes it can say
so. Delete that database and nothing is lost but the history.

## What findings mean

- **index_is_stale** -- the derived index is behind the Markdown. Run
  `never4ga index`. Harmless; the vault is canonical either way.
- **duplicate_id** -- two documents claim one identity. This is the serious one:
  fix it before anything else, because identity is what everything else points
  at.
- **broken_link** -- a link resolves to nothing. Check for a rename before
  assuming the target is missing.
- **a validation error** -- the document does not meet Schema v0.1. `never4ga
  validate <path>` says exactly which field.

Report what you found. Do not repair a finding you were not asked to repair, and
never "fix" a document by deleting the part that failed.
""",
        version="0.2.0",
    ),
    "never4ga-decide": _skill(
        "never4ga-decide",
        "Records a decision as an ADR, following the process that keeps the "
        "decision record the owner's. Use when a choice is made that future "
        "work will be built on, or when implementation reveals a specification flaw.",
        """
# Record a decision

**An ADR records a decision the vault's owner made. You never author an accepted
one, and you never edit a specification on your own authority.**

The process, and every step of it matters:

1. **Draft it as proposed.**

   ```bash
   never4ga concept create decision "<title>" --workspace <id>
   ```

   The template starts at `lifecycle: proposed`. Leave it there.

   **Leave the ADR number out of the title.** It is allocated: the next number
   in the `Decisions/` folder it lands in, never the highest you have seen
   elsewhere in the workspace family (`core/02` §21.11). A folder that does not number
   yet gets one only with `--number`; one with two series needs `--series`. A
   title stating a number other than the next is refused.

   **Never delete a decision.** A number is never reused, and the record is
   what holds it: one that no longer stands stays in place as `rejected` or
   `superseded`, or is archived, where it is still counted. Deleting a
   folder's highest record hands its number to the next decision.

2. **Say plainly that it is not in effect.** A draft that reads as settled is
   worse than no draft, because the next session will build on it.

3. **Stop.** Present the problem, the options and a recommendation. Then wait.

4. **Only on an explicit ruling**, set `lifecycle: accepted`, dated to that
   decision, update the specification deliberately, and update any manifest in
   the same change.

An agent that proposes a decision and also accepts it has put its own judgement
into the project's decision record. That is the failure this Skill exists to
prevent.

## What a decision needs

Context, the decision itself stated so it could be acted on alone, the
alternatives and why they lost, the consequences, and the condition that should
reopen it. A decision with no alternatives is an announcement.
""",
        version="0.4.0",
    ),
    "never4ga-checkpoint": _skill(
        "never4ga-checkpoint",
        "Records what a session has done so far, so it survives an "
        "interruption and another tool can see it. Use mid-session, whenever "
        "something durable has happened.",
        """
# Checkpoint

Record what has happened since the last one. Nothing here touches the vault:
checkpoints accumulate outside it and `never4ga-wrap` turns them into the one
document that lasts.

```bash
never4ga checkpoint "<what happened, briefly>" --session <id>
```

The session id is the one `never4ga context startup` reported. If you have lost
it, run startup again -- that opens a new session rather than finding the old
one, which is the honest outcome.

## Saying more than what happened

Six flags, and the distinction between them matters:

```bash
never4ga checkpoint "cleared two defects" --session <id> \\
  --action "merged PR #37" \\
  --decision "the date prefix is decided by the folder, not the caller" \\
  --memory "GitHub Actions on this repo runs about fifteen minutes behind" \\
  --work "840: ready to close, the read-back discipline is in" \\
  --context "10_Workspaces/Acme/Context/project-state.md: Milestone 13 closed" \\
  --walkthrough "10_Workspaces/Acme/Walkthroughs/build-plan_3.-launch.md: step 4, the deploy"
```

- `--action` is a fact: a merged PR, a written concept, a deleted file.
- `--decision` is a ruling you are claiming was made. **Never4gA records it
  verbatim and never interprets it, and it is not an accepted decision until
  somebody writes the ADR.** Declaring one is not the same as taking one.
- `--memory` is something worth knowing next time that is not a decision.
- `--work` is a tracker item you think needs something. **Never4gA writes
  nothing to the tracker itself** -- a ticket either needs particular
  information, which somebody should write, or it does not. **You are that
  somebody**, so act on it before you wrap:

  ```bash
  never4ga work update 840 --field status=Closed --session <id> --apply
  ```

  Pass `--session` so the write is recorded against this session. `wrap` exits
  non-zero while a declared item has none, because a tracker that drifts out of
  date because every session left it for the next one is the failure this
  declaration exists to prevent.

- `--context` is a context document this session changed something in -- a
  fact it asserts that is no longer true because of what you did. Name it by
  id or vault path, then `: what changed`. **Update the document itself**;
  the declaration is a claim, and `wrap` exits non-zero while a declared
  document's content has not moved. Correct the sentence that is now wrong;
  never add a dated entry to it (`core/07` §10).
- `--walkthrough` is the phase walkthrough you wrote a step into. **Write the
  step itself** -- what, files, the exact commands, why, and how it was
  checked, under the walkthrough's `## Steps` -- then declare it here, by id or
  vault path and `: step N`. `wrap` holds it exactly as it holds `--context`.
  A phase that has no walkthrough yet gets one first:
  `never4ga walkthrough start <plan> "<phase>"`.

Never4gA cannot work any of this out for itself. It never reads a transcript,
and it makes no model call, so what you declare is exactly what it knows.

## How often

When something durable happens, not on a timer. A checkpoint per merged PR or
per resolved question is about right; one per message is noise that makes the
session log unreadable.
""",
        version="0.7.0",
    ),
    "never4ga-wrap": _skill(
        "never4ga-wrap",
        "Closes a session by writing the one activity log it leaves behind. "
        "Use at the end of a substantive session, after the work is done.",
        """
# Wrap up

Write the record this session leaves behind. One `activity_log`, in the
workspace's `Logs/<year>/`, dated from when the session started rather than
when you got round to wrapping it.

```bash
never4ga doctor                                   # leave the vault healthy first
never4ga wrap --session <id> --title "<what it was about>"
```

Without `--title` the first checkpoint's note becomes the title, which is
usually worse than one sentence of thought.

## It is idempotent

Wrapping twice updates the same document rather than writing a second. So
wrapping early is not a mistake: checkpoint some more, wrap again, and the log
grows.

**Pass `--title` again if the session turned out to be about something else.**
That is the usual case for wrapping twice -- an early title describes what had
happened so far, and the reason to wrap again is that more happened. A title
given on the second wrap replaces the old one and the file is renamed to match;
the output names where it moved from. Leave `--title` off and the log keeps the
title it has.

## What it will not do

It refuses a session with no checkpoints. An empty log claims a session
happened and says nothing about it.

**It writes nothing but the log** -- not to the vault, and not to a tracker.
Declarations made with `--decision` come back in its output, and turning one
into an ADR is a step you take:

```bash
never4ga concept create decision "<title>" --workspace <id>
```

That is deliberate. An agent never authors an accepted decision, and a `wrap`
that drafted ADRs on a timer would be exactly that failure with a schedule
attached.

## Close the work out yourself

**Where the work lives is the workspace's choice.** A `work_management` block
in its frontmatter points at an external tracker; without one, the vault is
the tracker and open work is `task` concepts under `Tasks/`. The commands
below are the tracker form. For a vault-kept task the same acts are
`never4ga concept` edits to its `lifecycle`, and the discipline is identical:
finish the bookkeeping yourself rather than leaving it.

If the session touched a work item, say so as you go:

```bash
never4ga checkpoint --session <id> --work "840: ready to close, read-back is in" "..."
```

**Then act on it before you wrap.** `wrap` still writes nothing to a tracker,
and the reasoning holds: a ticket either needs some particular information, in
which case somebody should write *that*, or it does not, in which case a note
saying a session wrapped is noise on an audit trail somebody else reads.

You are that somebody. You held the conversation and you know what changed, so
write the thing the ticket actually needs -- not that a session happened:

```bash
never4ga work update 840 --field status=Closed --session <id> --apply
never4ga work comment 840 "the read-back discipline is in; closing" --session <id> --apply
```

Pass `--session` so Never4gA can see the write happened. A declaration is a
claim it records without believing; a write is something it observed.

**`wrap` will not finish quietly while a declared item has no write against
it.** It writes the log and then exits non-zero, listing what was left. Do the
work and wrap again, or pass `--allow-open-work` when leaving it open is the
right call and you want that on the record.

Do not leave this for the person. Somebody working entirely through agents
should never have to reconcile a tracker by hand.

## The check only sees what you declared

`--work` is what arms it, so an item you never mentioned is one nothing will
ask you about. That is how work outlives the thing that finished it, and it is
not a rare shape: a change resolves something it was not made for, a defect
turns out to have been fixed weeks ago, somebody fixes it themselves and
mentions it in passing.

**So before you wrap, read what is still open against what this session
actually did.** Where that list lives depends on the workspace, and both
answers are normal:

- **It declares a tracker** -- there is a `work_management` block in its
  frontmatter -- so the tracker is where open work lives:

  ```bash
  never4ga work search --status New
  ```

- **It declares none**, so the vault is the tracker. Open work is `task`
  concepts under the workspace's `Tasks/`, and its dashboard's Tasks view is
  that list. The lifecycle runs `todo`, `in_progress`, `blocked`, `done`,
  `cancelled`.

For anything that looks finished, **confirm it against reality rather than
against its own description** -- run the command, read the code, ask the
system -- and then close it with what you found. Something merely stale still
costs a person a read, and reading it costs more than checking it did.

The gap here is never a mechanism. It is that nobody looked.

## Read the context you were given

Every wrap lists the context documents this session's startup carried, and
asks one question: **did this session change anything these assert?** Answer
it before you wrap, not after. These are the documents every later session
reads first, so a stale one misleads all of them -- a workspace's
`agent-context.md` once told sessions for three weeks that a rebrand was
complete, while the sessions doing that work knew it was not.

Read each against what you did. Where one is now wrong, correct it -- an
ordinary edit, yours because the change was -- and declare it:

```bash
never4ga checkpoint --session <id> --context "<id or path>: what changed" "..."
```

`wrap` holds you to the declaration. A document you were given must have new
content since the session opened; one you were not given must have been
modified since then. Otherwise it exits non-zero and names the document.
`--allow-open-context` closes out anyway when you declared one and then found
it needed nothing.

**Correct it in place; never append to it** (`core/07` §10). A context document,
and a workspace's Current State, says what is true now. Rewrite the sentence
your work made wrong, and leave the account of what you did to the log `wrap`
writes. A dated paragraph added at the top is the easy edit and the wrong one:
`project-state.md` grew to 126 KB that way, one session at a time, until no
startup pack could carry it and every session got its title alone. Context
documents are required reading and are never cut (`core/07` §10), so every one
costs every later session its full length. `doctor` reports a workspace whose
required reading passes its ceiling as `required_reading_too_large`. If yours
does, say so rather than adding to it.

## Bring the walkthrough up to date

If the session did a step of a plan's phase, the phase's walkthrough carries
it: what, files, the exact commands, why, and how it was checked. Write it
before you wrap -- you have the commands now, and nobody will later -- and
declare it:

```bash
never4ga checkpoint --session <id> --walkthrough "<id or path>: step N" "..."
```

`wrap` holds a declared walkthrough exactly as it holds a declared context
document, and the log lists the steps apart, under *Walkthrough steps this
session wrote*. When the phase's done-when is met, set its `lifecycle` to
`complete`.

## Before you finish

Say what you left undone. A handoff that omits the unfinished part is worse
than no handoff, and `wrap` cannot know what it is -- the log records what you
told it happened, not what did not.
""",
        version="0.9.0",
    ),
}
