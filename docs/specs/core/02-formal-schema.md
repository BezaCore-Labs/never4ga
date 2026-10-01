---
type: documentation
id: 01a03502-3e5c-772f-ba17-2700cd0806a0
schema: never4ga/0.1
title: "Never4gA Formal Schema Specification v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/02_FORMAL_SCHEMA.md"
---
# Never4gA Formal Schema Specification v0.1

**Status:** Draft for adoption  
**Phase:** C — Formal Schema  
**Schema identifier:** `never4ga/0.1`  
**Compatibility target:** Open Knowledge Format (OKF) v0.2 where applicable  
**Depends on:** Never4gA Vault Specification v0.1 with the Phase-C compatibility correction in `VAULT_SPEC_COMPATIBILITY_CORRECTION.md`

---

# 1. Purpose

This specification defines the canonical metadata contract for Never4gA Markdown concepts.

Its goals are to make the same files:

- readable and editable by humans;
- useful in Obsidian;
- understandable by Claude Code, Codex, Antigravity, and future agents;
- indexable without using an LLM;
- portable across tools;
- capable of representing provenance, lifecycle, staleness, relationships, and authority;
- extensible without making unknown fields or new types fatal.

The schema is intentionally **stronger than OKF's minimum requirements** while preserving OKF field semantics where those fields are used.

---

# 2. Compatibility Model

## 2.1 OKF compatibility floor

Never4gA adopts the following OKF v0.2 semantics directly:

- Markdown documents with YAML frontmatter;
- `type`;
- `title`;
- `description`;
- `resource`;
- `tags`;
- `sources`;
- `usage_window`;
- `generated`;
- `verified`;
- `status`;
- `stale_after`;
- standard Markdown links;
- reserved `index.md`;
- reserved `log.md`;
- permissive handling of unknown types and unknown fields;
- actor naming conventions.

Never4gA MUST NOT redefine these fields incompatibly.

## 2.2 Native Never4gA profile

A Never4gA vault is more than a pure OKF knowledge bundle. It may contain interoperability files governed by other formats, including future examples such as:

- `SKILL.md`;
- `AGENTS.md`;
- `CLAUDE.md`;
- Obsidian `.base` files;
- runtime configuration;
- generated tool adapters.

Therefore Never4gA defines a **native profile**:

> Canonical Never4gA concept documents follow the Never4gA schema and compatible OKF semantics, while explicitly declared foreign-format/tool-owned files may be exempt from OKF concept validation.

A future `export okf` capability SHOULD be able to materialize a strict OKF v0.2 bundle from Never4gA canonical concepts.

## 2.3 Validation levels

Three validation levels are defined.

### `okf`

Checks applicable OKF v0.2 requirements for concept/index/log files.

### `core`

Checks Never4gA base metadata and type placement rules.

### `strict`

Checks:

- core schema;
- registered type/profile constraints;
- controlled vocabularies;
- relation targets where resolvable;
- lifecycle rules;
- provenance rules;
- placement rules;
- extension registration.

A document may remain readable even when strict validation fails.

---

# 3. Document Classes

Never4gA recognizes three broad file classes.

## 3.1 Concept documents

Normal Markdown documents representing knowledge or system objects.

They contain YAML frontmatter and a Markdown body.

Examples:

- `workspace.md`;
- `area.md`;
- Knowledge notes;
- Entity records;
- Goals;
- Plans;
- Decisions;
- Research notes;
- Standards.

## 3.2 Reserved OKF navigation/history documents

### `index.md`

Reserved for progressive-disclosure directory navigation.

Except for the bundle-root `index.md`, it MUST NOT contain normal concept frontmatter.

### `log.md`

Reserved for OKF-style chronological directory update history.

It is distinct from Never4gA workspace `Logs/`, which contains ordinary concept documents describing sessions, meetings, or activities.

## 3.3 Foreign-format / tool-owned documents

Files whose primary format is governed by another standard or tool.

Examples may include Agent Skills `SKILL.md` or Obsidian `.base` files.

These MUST be explicitly located in a registered integration/system location or identified by a future foreign-format registry.

A directory `never4ga init` registered as foreign material (ADR-0039,
`core/01` §1) is such a location, and the record of those directories in
`50_System/system.md` is that registry's first form. Inside it, a document
without an `id` — with or without frontmatter of its own — is *untracked*, a
warning that names `adopt`, and never a `missing_id` error. Its frontmatter is
the writer's and is preserved untouched.

**Amended 2026-09-21 by ADR-0040.** Adopting such a document keeps what the
writer wrote. Every key of its own frontmatter whose value the adopted concept
does not carry unchanged -- a value that does not fit the field, a value
`--field` replaced, a key Never4gA owns, or a `type` that is not registered --
is kept verbatim under `extensions.adopted` (§20) and reported, rather than
refused or discarded. No value is mapped to a registered one.

Never4gA consumers MUST NOT silently rewrite these files as Never4gA concept documents.

---

# 4. Root OKF Declaration

The vault root SHOULD contain:

```text
index.md
```

with:

```yaml
---
okf_version: "0.2"
---
```

followed by normal OKF index content.

This is the only `index.md` in the vault permitted to carry frontmatter.

`home.md` remains the human-facing dashboard and is a normal Never4gA concept document.

---

# 5. Base Concept Schema

## 5.1 Required fields

Every canonical Never4gA concept document MUST contain:

```yaml
---
type: <registered-or-extension-type>
id: <uuidv7>
schema: never4ga/0.1
title: <human-readable-title>
created_at: "<ISO-8601-datetime-with-offset>"
---
```

### `type`

Lowercase `snake_case`.

Examples:

```yaml
type: workspace
type: entity
type: knowledge
type: decision
type: activity_log
```

Never4gA consumers MUST tolerate unknown type values.

### `id`

A stable UUID version 7 in canonical lowercase hyphenated representation.

Example:

```yaml
id: 0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31
```

The ID:

- is independent of filename and path;
- MUST NOT change when a file is renamed or moved;
- MUST be unique within a vault;
- SHOULD be globally unique enough to permit future vault merges/imports.

The path remains the OKF concept identifier for OKF consumers. `id` is Never4gA's stable identity layer.

### `schema`

For v0.1:

```yaml
schema: never4ga/0.1
```

It identifies the Never4gA base schema targeted by the document.

### `title`

Human-readable display title.

Filename and title may differ.

### `created_at`

Creation timestamp of the Never4gA concept.

Format:

```text
YYYY-MM-DDTHH:MM:SSZ
```

or another ISO 8601 datetime with explicit UTC offset.

Canonical writers SHOULD use UTC (`Z`) unless preserving the original offset has a meaningful reason.

`created_at` never changes.

---

## 5.2 Strongly recommended fields

```yaml
description: <one-sentence-summary>
generated:
  by: <actor>
  at: "<datetime>"
```

### `description`

A concise one-sentence explanation optimized for:

- human previews;
- index generation;
- search results;
- context assembly.

Major durable objects such as workspaces, entities, knowledge notes, standards, maps, and life areas SHOULD have descriptions.

### `generated`

Uses OKF semantics.

```yaml
generated:
  by: human:owner
  at: "2026-08-22T19:00:00Z"
```

or:

```yaml
generated:
  by: claude-code/claude-sonnet
  at: "2026-08-22T19:00:00Z"
```

`generated.at` represents the current content's last meaningful change, not filesystem mtime.

Agent/process-created or agent/process-rewritten canonical content MUST include `generated`.

Human-authored files SHOULD include it when tooling can maintain it reliably.

Never4gA MUST NOT fabricate a `generated.at` value it cannot substantiate.

---

# 6. Base Optional Fields

A normal concept may include:

```yaml
aliases: []
resource:
tags: []
domains: []
status:
stale_after:
verified:
sources:
usage_window:
authority:
lifecycle:
parent:
workspace:
relations:
profiles: []
extensions:
```

Each is defined below.

---

# 7. Naming and Scalar Conventions

## 7.1 Metadata keys

Never4gA-defined keys use lowercase `snake_case`.

Examples:

```text
created_at
workspace_type
entity_type
target_date
```

## 7.2 Type and registry values

Machine identifiers use lowercase `snake_case` unless inherited from an external standard.

Examples:

```text
software_product
home_maintenance
in_progress
superseded_by
```

## 7.3 Human text

Human-facing names remain ordinary natural-language strings.

## 7.4 Timestamps

All timestamp-valued fields use ISO 8601 with an explicit offset.

Writers SHOULD quote timestamps in YAML to avoid parser-dependent implicit typing.

Example:

```yaml
created_at: "2026-08-22T19:00:00Z"
```

---

# 8. Aliases

```yaml
aliases:
  - Postgres
  - PostgreSQL Database
```

`aliases` contains alternative human names for the same concept.

Aliases support:

- human retrieval;
- Obsidian alias behavior;
- entity resolution;
- duplicate detection.

Aliases MUST NOT be used as stable identifiers.

---

# 9. Classification

Never4gA deliberately avoids creating multiple overlapping taxonomy systems.

V0.1 uses only:

1. controlled broad `domains`;
2. lightweight `tags`;
3. explicit links and typed relationships.

There is no core `topics` field in v0.1.

---

## 9.1 `domains`

```yaml
domains:
  - software_development
  - artificial_intelligence
```

Domains are:

- broad;
- stable;
- controlled;
- non-exclusive;
- non-hierarchical in v0.1.

Valid domain values come from a Never4gA Domain Registry.

A concept MAY belong to multiple domains.

Domains MUST NOT be automatically created merely because an agent invents a new label.

Unknown domain values are a strict-validation warning until registered.

### Domain design rule

A domain should answer:

> Which broad area of knowledge or responsibility is this relevant to?

It should not answer:

> What exact thing is this about?

Exact subjects should normally be represented by links/Entities/relations.

---

## 9.2 `tags`

```yaml
tags:
  - python
  - authentication
  - troubleshooting
```

Tags are lightweight cross-cutting labels.

They:

- are optional;
- are less authoritative than domains;
- may be user-created;
- SHOULD use normalized lowercase identifiers;
- SHOULD be deduplicated/merged by maintenance tooling when synonyms proliferate.

Tags MUST NOT be treated as a formal ontology.

Agents SHOULD prefer an existing tag when it expresses the same meaning.

---

# 10. OKF Content Status

Never4gA preserves OKF v0.2 `status` semantics exactly:

```yaml
status: draft
```

Allowed:

```text
draft
stable
deprecated
```

Absent `status` means `stable` under OKF semantics.

This field describes the **maturity/currentness of the document content**.

It MUST NOT be reused for:

- task completion;
- workspace state;
- goal state;
- decision state.

Those use `lifecycle`.

---

# 11. Never4gA Semantic Lifecycle

```yaml
lifecycle: active
```

`lifecycle` describes the state of the object represented by the document.

Lifecycle vocabularies are type-specific.

Examples:

- a workspace can be `active`;
- a task can be `done`;
- a decision can be `superseded`.

A type schema MAY require `lifecycle`.

Unknown lifecycle values are tolerated at base validation and rejected/warned according to the registered type/profile during strict validation.

This separation is intentional:

```yaml
status: stable
lifecycle: paused
```

is valid for a well-written workspace document representing a paused workspace.

---

# 12. Authority

```yaml
authority: authoritative
```

`authority` describes the operational/normative role of the content.

Allowed core values:

### `authoritative`

Accepted as governing/current within its declared scope.

Examples:

- an accepted architectural decision;
- a current standard;
- a user-confirmed project requirement.

### `informational`

Durable information intended to inform decisions but not govern behavior.

Typical default for normal knowledge notes.

### `provisional`

Not yet accepted as durable/current truth.

Examples:

- proposal;
- working hypothesis;
- candidate decision.

### `derived`

A synthesis or inference produced from other material and not independently asserted.

Typical starting authority for machine-derived summaries unless promoted.

`authority` is **not a confidence score** and is **not a trust score**.

Never4gA v0.1 intentionally defines no generic numeric `confidence` field.

Trust is represented through provenance and verification.

---

# 13. Provenance — `sources`

Never4gA uses OKF `sources` semantics.

Example:

```yaml
sources:
  - id: sqlite-fts5
    resource: https://sqlite.org/fts5.html
    title: SQLite FTS5 Documentation
    author: human:sqlite-docs
    last_modified: "2026-01-01T00:00:00Z"
```

Each source entry MUST contain `resource`.

`resource` may be:

- an absolute URL;
- a vault/bundle-relative path;
- a relative path;
- another valid scope descriptor permitted by OKF.

If a body claim requires attribution, use an OKF-compatible Markdown footnote keyed to `sources[].id`.

Never4gA MUST NOT invent source credibility scores.

---

# 14. Generation and Verification

## 14.1 Actor convention

Never4gA adopts OKF actor forms:

```text
human:<id>
<producer>/<version>
process:<id>
```

For a personal vault, the default human actor SHOULD be:

```text
human:owner
```

unless the user intentionally configures another stable local actor ID.

Do not embed email addresses or other unnecessary personally identifying values in actor IDs.

Examples:

```yaml
generated:
  by: human:owner
  at: "2026-08-22T19:00:00Z"
```

```yaml
generated:
  by: codex/gpt-5
  at: "2026-08-22T19:00:00Z"
```

```yaml
verified:
  - by: human:owner
    at: "2026-08-22T20:00:00Z"
```

## 14.2 Verification semantics

Never4gA inherits OKF's derived trust tiers:

- no `verified` → unverified;
- only non-human verification → machine-confirmed;
- any `human:` verifier → human-reviewed.

Verification does not automatically change `authority`.

A human may verify that a provisional proposal accurately reflects its sources while leaving it provisional.

---

# 15. Freshness and Staleness

Never4gA adopts OKF `stale_after`.

```yaml
stale_after: "2026-11-22T00:00:00Z"
```

A concept is stale when:

```text
now >= stale_after
```

`stale_after` is optional.

Absence means:

> no explicit automatic staleness instant has been asserted.

It does NOT mean:

> this content can never become stale.

## 15.1 Freshness policies

Never4gA v0.1 does not store a generic relative TTL such as `review_every: 90d` on every document.

Instead, future maintenance policies may determine or propose `stale_after` based on:

- type;
- domain;
- source characteristics;
- workspace profile;
- user policy.

This avoids two competing freshness sources of truth.

## 15.2 Maintenance behavior

Stale content SHOULD:

- remain available;
- be visibly flagged;
- be downgraded during context assembly when appropriate;
- trigger review/verification workflows;
- never be silently deleted.

---

# 16. Scope Fields

## 16.1 `workspace`

```yaml
workspace: 0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31
```

`workspace` is the stable ID of the workspace that owns/scopes the concept.

Required for workspace-scoped types such as:

- goal;
- plan;
- task;
- decision;
- research_note;
- context;
- activity_log.

The filesystem path is expected to agree with this field.

When they disagree:

1. strict validation reports an error;
2. Never4gA MUST NOT silently guess which is authoritative;
3. repair requires explicit resolution.

## 16.2 `parent`

```yaml
parent: 0198d71c-f0ad-71bd-949a-9276f9ce4e84
```

`parent` represents structural hierarchy between objects of a compatible kind.

Examples:

- child workspace → parent workspace;
- subgoal → parent goal;
- subtask → parent task.

`parent` is not provenance.


## 16.3 `area`

**Added 2026-08-31 by ADR-0028.**

```yaml
area: 01a04f5f-5581-737b-80b2-16181bec2321
```

`area` is the stable ID of the life area that owns/scopes the concept.

Required, in place of `workspace`, for a workspace-scoped type placed in a life
area under ADR-0024 (`core/01` §7):

- goal;
- plan;
- task;
- decision;
- research_note;
- activity_log.

Requirement follows placement, because scope does: the rule is *name your
scope*, and the field is decided by where the document sits.

`area` and `workspace` are mutually exclusive on one document. A concept has one
scope, and a document carrying both is a contradiction rather than a ranking
problem.

An area is not a workspace (ADR-0024: it is carried, not pursued), so an `area`
id MUST NOT be used where a `workspace` id is expected. A *workspace* that lives
under `20_Life/<Area>/Workspaces/` names its area with `parent`, not `area`:
§16.3 scopes a document to a responsibility, §16.2 places a workspace in a
hierarchy.

The filesystem path is expected to agree with this field.

When they disagree, §16.1's three rules apply unchanged:

1. strict validation reports an error;
2. Never4gA MUST NOT silently guess which is authoritative;
3. repair requires explicit resolution.

---

# 17. Typed Relationships

Markdown links express human-readable relationships.

Never4gA additionally defines typed machine-readable relations.

```yaml
relations:
  - type: depends_on
    target: 0198d71c-f0ad-71bd-949a-9276f9ce4e84
  - type: applies_to
    target: 0198d790-38a3-75c1-ab41-60204de13309
```

## 17.1 Relation fields

Each relation contains:

### `type`

Registered lowercase `snake_case` relation identifier.

### `target`

Stable Never4gA concept UUID.

## 17.2 Core relation vocabulary

V0.1 reserves:

### `related_to`

General semantic connection when no more precise core relation applies.

### `depends_on`

Source object requires target.

### `blocks`

Source prevents progress/completion of target.

### `implements`

Source implements/realizes target.

### `supports`

Source provides evidence/help for target.

### `applies_to`

Source applies to target scope/object.

### `superseded_by`

Source has been replaced by target.

### `closes`

Source declares the target's work finished.

Written on the record of completion -- an `activity_log` closing a `plan` -- at
the moment the connection is known, rather than on the plan, which would be
edited by every report about it.

Example:

```yaml
# in a completion report
relations:
  - type: closes
    target: <plan-id>
```

A plan that something `closes` whose own `lifecycle` is not terminal is a
reportable inconsistency (ADR-0022).

### `excepts`

Source overrides target for the scope the source belongs to.

**Added 2026-08-31 by ADR-0029.** Written on a child workspace's own `standard`,
naming the inherited standard it overrules (`core/03` §32, Q-048). Only an
explicit exception counts: a child cannot override by silence or by saying
something different in passing.

The reason is the document body, because that is what a standard is.

Example:

```yaml
# in a child workspace's own standard
relations:
  - type: excepts
    target: <inherited-standard-id>
```

Registering the relation does not enforce it. Nothing yet applies "closest rule
wins" — the assembler selects standards by metadata filter and carries both.

The reverse edge of registered relations MAY be derived by the index and need not be redundantly stored.

Example:

If:

```text
old decision --superseded_by--> new decision
```

the graph may expose:

```text
new decision --supersedes--> old decision
```

without storing a second canonical relation.

## 17.3 Relation extensions

New relation types require registration in the Relation Registry.

Consumers MUST preserve unknown relation types.

Agents MUST NOT invent a new relation type when an equivalent registered relation already exists.

---

# 18. Links

Canonical prose SHOULD prefer standard Markdown internal links rather than Obsidian-only wikilinks when interoperability matters.

Example:

```markdown
[Python](../../30_Knowledge/Notes/python.md)
```

Never4gA's Obsidian profile SHOULD configure Obsidian to generate Markdown links rather than Wikilinks.

Obsidian-specific block references MAY be used for purely local convenience but MUST NOT be required for portable semantic relationships.

Typed `relations` use stable IDs; Markdown links remain human-readable and path-based.

This gives both:

- stable machine identity;
- readable portable navigation.

---

# 19. Profiles and Schema Extensions

## 19.1 `profiles`

```yaml
profiles:
  - never4ga/workspace/software/0.1
```

`profiles` declares additional schema contracts applied to the concept.

The base schema is always identified by:

```yaml
schema: never4ga/0.1
```

Profiles allow extensions without changing base type semantics.

Examples may eventually include:

```text
never4ga/workspace/organization/0.1
never4ga/workspace/software/0.1
never4ga/workspace/event/0.1
```

Phase D defines workspace profiles.

## 19.2 Unknown profiles

Unknown profiles MUST be preserved.

A consumer that does not understand a profile:

- may process the base schema;
- must not discard unknown profile fields;
- should report reduced validation capability rather than rejecting the concept outright.

---

# 20. Extension Data

Producer/user-specific metadata that is not part of the registered Never4gA schema SHOULD live under:

```yaml
extensions:
  <namespace>:
    ...
```

Example:

```yaml
extensions:
  my_local_workflow:
    review_bucket: weekly
```

The namespace should be distinctive.

Registered Never4gA fields remain top-level for usability in Markdown tooling and Obsidian.

Extensions MUST NOT redefine the meaning of core fields.

Consumers MUST preserve unknown extensions during round-tripping.

---

# 21. Type Registry v0.1

The following core types are defined.

**Amended 2026-08-30 by ADR-0024.** Seven types gain a `20_Life/<Area>/`
location in addition to what their entries below state: `resource`,
`activity_log`, `plan`, `goal`, `task`, `research_note` and `decision`. An
`activity_log` in an area carries the area as its scope rather than requiring a
`workspace`; **the field that carries it is `area` (§16.3), registered
2026-08-31 by ADR-0028**, which applies to all six of the workspace-scoped types
above and not to `activity_log` alone. `workspace` additionally accepts `20_Life/<Area>/Workspaces/<Name>/workspace.md`
with the area as `parent` (`core/03` §30). `knowledge` gains nothing — the
practice-versus-reference line in `core/01` §7 is the rule. Recorded here as one
amendment rather than rewritten into seven entries so the change and its source
stay legible; the registry *implementation* follows through the ordinary gate,
and until it ships `validate` still reports the pre-ADR-0024 locations.

---

## 21.1 `dashboard`

Typical location:

```text
/home.md
```

Purpose:

Human-facing navigation/dashboard.

Required beyond base:

None.

Recommended:

```yaml
authority: informational
```

---

## 21.2 `workspace`

Canonical manifest:

```text
<Workspace>/workspace.md
```

Required beyond base:

```yaml
workspace_type: <registered-workspace-type>
lifecycle: <workspace-lifecycle>
```

Optional:

```yaml
parent: <parent-workspace-id>
repositories: [<repository-name>, ...]
work_management: <connection-reference>
cover: <vault-relative-image-path>
```

A top-level workspace omits `parent`.

`repositories` (`core/03` §5.5, plural since it was written) and
`work_management` (`core/03` §16) were registered when the milestones that
introduced them shipped; they are listed here **as of 2026-08-30**, because this
block had said `parent` and nothing else while the registry carried all three.

`cover` names an image the Obsidian card views render for the workspace, and was
registered 2026-08-30. Never4gA both writes it and reads it, so a warning on it
was a warning nobody could clear — the failure #94 removed for `index_is_stale`.
It is registered on `workspace` only.

Exact workspace types and lifecycle vocabulary are finalized in Phase D.

---

## 21.3 `life_area`

Canonical manifest:

```text
20_Life/<Area>/area.md
```

Required beyond base:

```yaml
lifecycle: active
```

Initial lifecycle vocabulary:

```text
active
retired
```

Recommended authority:

```yaml
authority: authoritative
```

---

## 21.4 `knowledge`

Location:

```text
30_Knowledge/Notes/
```

Purpose:

Durable reusable knowledge.

Recommended:

```yaml
authority: informational
description: ...
domains: [...]
```

Optional:

```yaml
sources:
verified:
stale_after:
```

---

## 21.5 `map`

Location:

```text
30_Knowledge/Maps/
```

Purpose:

Curated navigation/synthesis.

Recommended:

```yaml
authority: informational
```

Maps primarily connect concepts through normal Markdown links.

---

## 21.6 `entity` — retired

**Retired 2026-09-05 by ADR-0032**, with the `40_Entities/` root it lived in
(`core/01` §9). An entity record was a knowledge note in a second place: a
`LINK_RELATION` edge does not read the target's type, `aliases` resolve on any
document, and `context/structural.py` already grouped `entity` with `knowledge`
and `resource` as reference material reached by searching. The number is not
reused.

`entity_type` is retired with it, and with it the Entity Type Registry that
§22 used to list. An `entity_type` field surviving on an old document is an
unknown field, which §17.3 requires be tolerated and preserved.

A **person** was the one thing with no other home; it is §21.23.

## 21.7 `context`

Location:

```text
<Workspace>/Context/
```

Required:

```yaml
workspace: <workspace-id>
```

Purpose:

Current-state/orientation material.

---

## 21.8 `goal`

Location:

```text
<Workspace>/Goals/
```

Required:

```yaml
workspace: <workspace-id>
lifecycle: <goal-lifecycle>
```

Initial vocabulary:

```text
proposed
active
achieved
abandoned
```

Optional:

```yaml
target_date: "YYYY-MM-DD"
parent: <goal-id>
```

---

## 21.9 `plan`

Location:

```text
<Workspace>/Plans/
<Workspace>/Releases/     (software profile; core/03 section 10 defines
                           Releases as release plans first -- ADR-0017)
```

Required:

```yaml
workspace: <workspace-id>
lifecycle: <plan-lifecycle>
```

Initial vocabulary:

```text
draft
active
completed
abandoned
```

A plan may relate to goals using:

```yaml
relations:
  - type: implements
    target: <goal-id>
```

---

## 21.10 `task`

Location:

```text
<Workspace>/Tasks/
```

Required:

```yaml
workspace: <workspace-id>
lifecycle: <task-lifecycle>
```

Initial vocabulary:

```text
todo
in_progress
blocked
done
cancelled
```

Optional:

```yaml
priority: normal
due_date: "YYYY-MM-DD"
parent: <task-id>
```

Priority vocabulary:

```text
low
normal
high
critical
```

---

## 21.11 `decision`

Location:

```text
<Workspace>/Decisions/
```

Required:

```yaml
workspace: <workspace-id>
lifecycle: <decision-lifecycle>
```

Initial vocabulary:

```text
proposed
accepted
rejected
superseded
```

Recommended authority:

```text
proposed    → provisional
accepted    → authoritative
rejected    → informational
superseded  → informational
```

A superseded decision MUST identify its replacement when known:

```yaml
relations:
  - type: superseded_by
    target: <new-decision-id>
```

**Added 2026-09-27 by ADR-0044.** A decision's number is part of its filename,
`adr-[<series>-]NNNN_<slug>.md`, and it is scoped to the `Decisions/` folder
that holds it. Creating a decision in a folder that already numbers its records
MUST allocate the next number in that folder and series: the highest present
plus one. The title then begins `ADR-NNNN — `. A title that states a different
number MUST be refused. A folder with no numbered records stays unnumbered
unless a number is asked for, and the first is then 0001. A folder carrying
more than one series requires the series to be named. Gaps are permitted.
Adoption does not renumber.

---

## 21.12 `research_note`

Location:

```text
<Workspace>/Research/
```

Required:

```yaml
workspace: <workspace-id>
lifecycle: <research-lifecycle>
```

Initial lifecycle:

```text
active
complete
abandoned
```

Recommended:

```yaml
authority: provisional
sources: [...]
```

Research conclusions promoted into general durable knowledge should normally become separate `knowledge` concepts rather than turning the research history itself into the only reusable record.

---

## 21.13 `resource`

Typical location:

```text
<Workspace>/Resources/
<Workspace>/<profile extension>/   (Strategy, Finance, Operations, Brand,
                                    Requirements, Testing, Releases -- ADR-0017)
```

Supporting material appears wherever the material it supports does, so a
`resource` is accepted in any profile extension folder core/03 sections 9 and 10
define.

Required when describing an external/binary resource:

```yaml
resource: <URI-or-path>
```

Purpose:

Workspace-specific supporting material that deserves a Markdown concept record.

---

## 21.14 `activity_log`

Location:

```text
<Workspace>/Logs/
```

Required:

```yaml
workspace: <workspace-id>
occurred_at: "<datetime>"
```

Purpose:

Human/agent session history, meeting record, work journal, retrospective, or activity record.

Optional:

```yaml
covers: "<start>/<end>"   # a roll-up's range (ADR-0016); ADR-0043
```

A log that carries `covers` is a roll-up, not a session's handoff. It is never
chosen as a workspace's newest activity log for required reading (ADR-0043).

This type MUST NOT use reserved filename `log.md`.

---

## 21.15 `standard`

Location:

```text
50_System/Standards/
<Workspace>/Strategy/       ┐
<Workspace>/Brand/          │ profile extension folders, core/03
<Workspace>/Finance/        │ sections 9 and 10 -- ADR-0017
<Workspace>/Operations/     │
<Workspace>/Requirements/   │
<Workspace>/Testing/        ┘
```

A standard in `50_System/Standards/` is vault-wide; one in a workspace's profile
folder is scoped to that workspace. A palette specification, a bookkeeping
cadence, a contract workflow, a requirement and a testing strategy all
prescribe, which is what a standard is; the location says who it binds.

Recommended:

```yaml
authority: authoritative
```

A standard may use:

```yaml
relations:
  - type: applies_to
    target: <concept-id>
required_reading: false
```

`required_reading` is a boolean saying whether every startup the standard
applies to reads it in full (`core/07` §10, ADR-0050). Without it, a standard
in `50_System/Standards/` is required reading and a workspace's own standard is
read on demand: listed with its `description`, and opened when the work
touches it. Any other value is a validation finding.

---

## 21.16 `schema_definition`

Location:

```text
50_System/Schemas/
```

Purpose:

Human-readable/schema-governance description.

Recommended:

```yaml
authority: authoritative
```

---

## 21.17 `registry`

Location:

```text
50_System/Schemas/
```

Purpose:

Controlled vocabulary registry.

Examples:

- Domain Registry;
- Entity Type Registry;
- Relation Registry;
- Workspace Type Registry.

Registries SHOULD be authoritative.

Machine-readable registry values may be represented in frontmatter using registered schema fields defined by the registry profile.

---

## 21.18 `documentation`

Locations:

```text
<workspace>/Documentation/
50_System/Documentation/
<workspace>/Architecture/
<life-area>/
```

Purpose:

Documentation of anything the vault holds — a project, a product, a workspace, a
life area, or Never4gA itself.

This section read *"Documentation of Never4gA itself"* with
`50_System/Documentation/` as its only home until 2026-09-11 (ADR-0038). That
left no registered type for documentation of a project, which is most
documentation that exists: a workspace wanting a README-equivalent had to borrow
a type meaning something else — `context` (current-state orientation), `knowledge`
(reference material in `30_Knowledge/`), or `resource` (supporting material). No
other type in the registry is confined to one project, and the narrowing bought
nothing. `50_System/Documentation/` remains where Never4gA documents itself
because that is where it sits, not because the type forbids anywhere else.

A workspace's `Documentation/` is the default home when a workspace is named.
`Architecture/` stays accepted for design documents and technical structure
(`core/03` §10), which is where a product's own specifications already live.

`documentation` requires no scope field, because one of its homes is not in one.
Creation fills `workspace` in from placement wherever placement implies one, as
it does for `resource`.

---

## 21.26 `runbook`

Locations:

```text
<workspace>/Runbooks/
<life-area>/
```

Required beyond the base fields:

```text
workspace   (or `area`, by placement — §21 scope-follows-placement)
```

Purpose:

How to operate a thing: start it, stop it, recover it, what to do when it
breaks, the sequence to run.

Registered 2026-09-11 by ADR-0038. The registry covered design and governance —
`decision`, `plan`, `context`, `standard`, `requirement` — and reference —
`knowledge`, `resource` — and had nothing for operational instruction.

It is separate from `documentation` rather than a shape of it because retrieval
has to tell them apart. When something is broken, *"what do I do when this
breaks"* is a different need from *"how is this built"*, and a type is how the
system knows which it is holding. Folding it into `documentation` would rebuild
the catch-all this type exists to empty.

A life area may hold one: operating a thing is not only a software concern.

---

## 21.19 `integration`

Location:

```text
50_System/Integrations/
```

Purpose:

Canonical documentation/configuration describing an integration.

Tool-owned files governed by the integration may be foreign-format files and are not automatically `integration` concepts.

---

## 21.20 `system_manifest`

Canonical location:

```text
50_System/system.md
```

Purpose:

The stable manifest for the vault itself. Its `id` is the vault identity, which
survives moving or renaming the vault directory (`core/01` section 10,
`core/05` section 6).

Required beyond base:

None.

Recommended:

```yaml
authority: authoritative
```

A vault has exactly one `system_manifest`.

Added by ADR-0006, reconciling this registry with `core/05` section 6, which
defines the type and describes it as "an additive Schema v0.1 type".

---

## 21.21 `skill_provenance`

Canonical location:

```text
50_System/skill-provenance_<name>.md
50_System/agent-provenance_<name>.md
```

The class is in the filename because a Skill and an Agent may legitimately
share a name, and one record path for both would silently overwrite. The type
is `skill_provenance` for both: renaming it would be churn for a distinction
the path already carries.

Purpose:

Where a vendored Skill or Agent came from, and what it contained when it
arrived. `50_System/Skills/` and `50_System/Agents/` are foreign-format islands
(section 3.3): nothing in them is validated or indexed, so a record kept beside
the material would be invisible to `doctor` and to the index, and whole-directory
deployment would copy vault bookkeeping into every client. The record therefore
lives outside the island and names its subject by path.

Required beyond base:

```yaml
skill_path: 50_System/Skills/<name>    # the subject, vault-relative
origin:
  kind: git | http | local | authored
  fetched_at: <timestamp>
integrity:
  algorithm: sha256
  tree_digest: <hex>
```

`origin.kind: git` additionally requires `url` and `commit`; `ref` and `subpath`
are recommended, and `license` and `author` are recommended wherever the
material carries them, because a licence obligation that is not recorded cannot
be honoured.

Recommended:

```yaml
authority: informational
integrity:
  files: {<relative path>: <hex>}
upstream:
  last_checked_at: <timestamp>
  last_seen_commit: <hex>
  status: current | ahead | unknown
local:
  modified: false
```

`tree_digest` is computed over the subject directory by the construction
Never4gA already uses for the seed hash on its own shipped Skills: for every
file, sorted by relative path, feed `path`, a unit separator, the file's text,
and a unit separator into one SHA-256. Deterministic, order-independent, and
independent of mode and mtime.

Stated as a reuse rather than a definition on purpose. ADR-0027 requires one
construction serving first-party and vendored material alike; a second digest
that differed only in its separator would make two records incomparable for no
gain. `integrity.files` is the separate, additional thing: a per-file SHA-256,
which turns "something changed" into "this file changed".

The three fields answer three different questions and none substitutes for
another. `origin` says where it came from; `integrity` says what arrived;
`upstream` says what is there now. Origin without integrity detects nothing —
the arrangement ADR-0027 examined in prior art and declined. Integrity without
origin can never answer whether upstream moved.

A subject directory with no `skill_provenance` is reported, not repaired: what
a vendored artifact came from is not derivable from its contents.

Added by ADR-0027, which extends the two hops ADR-0021 mapped — shipped to
vault, vault to client — with the third that governs material Never4gA did not
author.

## 21.22 `finding_dismissal`

**Added 2026-08-31 by ADR-0029.**

Location:

```text
50_System/
```

Purpose:

A person's judgement that a maintenance finding is wrong, or is right and does
not matter. Canonical in the vault rather than in the ledger: a judgement living
only in a derived table a rebuild can erase is a fact with no home (Q-051,
ADR-0023). The ledger caches it.

Required beyond base:

```yaml
finding_code: <the rule that fired>
fingerprint: <the ledger's key for this finding>
reason: <why it is dismissed>
```

Optional:

```yaml
finding_path: <the path the finding named>
```

`fingerprint` rather than a path, because the fingerprint is what the ledger
keys on and what re-detection compares; a dismissal naming only a path would
silently cover a different finding about the same file. `finding_path` is
carried beside it as the thing a person reads, and is prefixed because an
unprefixed `path` in frontmatter reads as the document's own.

Recommended:

```yaml
authority: informational
```

Registering the type does not enforce it. Nothing yet reads a dismissal into the
ledger, so a dismissed finding still reports.

## 21.23 `person`

**Added 2026-09-05 by ADR-0032.**

Location:

```text
20_Life/<Area>/
```

Purpose:

A person. Registered when `40_Entities/` was dissolved, and the one case that
survived the argument that dissolved it: the three things an entity record
claimed to give — graph traversal, `aliases`, and reaching a context pack — are
each something any document already has, so an entity record was a knowledge
note in a second place. A person is different only because nobody writes a
*knowledge note* about a person. `30_Knowledge/` is for subjects; a person is
not a subject.

Required beyond base:

nothing.

Recommended:

```yaml
aliases: [...]
authority: informational
```

**A life area, and only a life area.** `20_Life/Relationships/` describes itself
as "a lightweight personal record — not a CRM — for people, family, community
and social connections", and routes professional relationships to Career and to
the owning workspace; a person met through a club belongs to the area that club
is in.

**No `workspace` field.** A person is not scoped to a project. Sam Rivera is
a client's stakeholder, a lead for one product and a member of a local club at
once, and none of those owns them — the documents that need them link to them.

`aliases` carries the second name a person is known by, exactly as it does
anywhere else: `Coach Sam` and `Sam Rivera` reach one document because the
document says so.

---

## 21.24 `course_unit`

Registered by ADR-0034 (2026-09-07). A course is a workspace (ADR-0024 part
2); this is the page a student works a unit from.

Location:

```text
<Workspace>/Units/
20_Life/<Area>/          (a life area, ADR-0024 part 1's allowance)
```

Required:

```yaml
workspace: <workspace-id>
lifecycle: <course-unit-lifecycle>
```

Initial vocabulary:

```text
not_started
in_progress
complete
```

Optional:

```yaml
unit: <number-or-label>
opens: "YYYY-MM-DD"
due: "YYYY-MM-DD"
```

Purpose:

One unit of a course: the reading, the videos, the notes and the questions,
so what is open and what is due is a query over frontmatter rather than a
table maintained by hand. `knowledge` is reference material in
`30_Knowledge/`, `research_note` is research, and `resource` is the material
that *supports* the work rather than the work itself.

---

## 21.25 `course_assignment`

Registered by ADR-0036 (2026-09-07), which supersedes the ADR-0034 clause
that filed this material under `resource`.

Location:

```text
<Workspace>/Units/       (any depth -- Units/Unit-3/ is home, not an alternate)
20_Life/<Area>/
```

Required:

```yaml
workspace: <workspace-id>
lifecycle: <course-assignment-lifecycle>
```

Initial vocabulary:

```text
not_started
in_progress
submitted
graded
```

Optional:

```yaml
unit: <number-or-label>
due: "YYYY-MM-DD"
grade: <what-it-scored>
```

Purpose:

The graded work a unit carries -- an assignment prompt, a rubric, a
discussion question -- living beside the unit page it belongs to. It opens,
is due, is submitted and is graded, none of which `resource` carries. The
syllabus, companion articles and reading lists stay `resources`: they support
the work; this *is* the work's record.

---

# 22. Controlled Registries

The following registries are required conceptually by v0.1:

1. Type Registry
2. Domain Registry
3. Relation Registry
4. Workspace Type Registry
5. Profile Registry

The Entity Type Registry was the sixth until 2026-09-05, when ADR-0032 retired
the `entity` type it served.

Initial bootstrapping may hard-code the core values defined in this specification.

Before the runtime is considered mature, the canonical registries SHOULD be represented inside `50_System/Schemas/` so the system can describe its own vocabulary.

---

# 23. Placement Validation

Filesystem location is not the sole semantic truth, but standard placement is enforceable.

Examples:

```text
30_Knowledge/Notes/X.md
```

with:

```yaml
type: decision
```

is a strict-validation error.

Likewise:

```text
<Workspace>/Decisions/X.md
```

with:

```yaml
type: knowledge
```

is normally a strict-validation error unless a future profile explicitly permits it.

The repair process must be explicit:

- change the type;
- move the file;
- or register an extension.

Never4gA MUST NOT silently rewrite user organization.

---

# 24. Authority + Generation Examples

## 24.1 Human-authored durable knowledge

```yaml
---
type: knowledge
id: 0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31
schema: never4ga/0.1
title: Hybrid Retrieval
description: Combines lexical and semantic retrieval to improve recall and precision.
created_at: "2026-08-22T19:00:00Z"
generated:
  by: human:owner
  at: "2026-08-22T19:00:00Z"
authority: informational
domains:
  - artificial_intelligence
tags:
  - retrieval
  - rag
---
```

## 24.2 AI-produced unreviewed synthesis

```yaml
---
type: knowledge
id: 0198d70c-679f-7838-9bf6-3363a583f081
schema: never4ga/0.1
title: Candidate Vector Database Comparison
description: Agent-produced synthesis comparing potential vector backends.
created_at: "2026-08-22T19:10:00Z"
generated:
  by: claude-code/claude-sonnet
  at: "2026-08-22T19:10:00Z"
authority: derived
status: draft
---
```

## 24.3 Human-reviewed AI synthesis

```yaml
generated:
  by: claude-code/claude-sonnet
  at: "2026-08-22T19:10:00Z"
verified:
  - by: human:owner
    at: "2026-08-22T20:00:00Z"
authority: informational
status: stable
```

The original producer remains visible after human verification.

---

# 25. Workspace Example

```yaml
---
type: workspace
id: 0198d741-0cf9-7360-b426-964081ed0a39
schema: never4ga/0.1
title: Sparrow
description: Product workspace for the Sparrow note-taking app.
created_at: "2026-08-22T19:00:00Z"

generated:
  by: human:owner
  at: "2026-08-22T19:00:00Z"

status: stable
authority: authoritative

workspace_type: software_product
lifecycle: active

parent: 0198d750-3f96-7eae-b190-a888afe0b7ad

domains:
  - software_development

tags:
  - note-taking
---
```

---

# 26. Decision Example

```yaml
---
type: decision
id: 0198d76b-3872-7480-a19e-c0807d751431
schema: never4ga/0.1
title: Keep software repositories outside the vault
description: Application source repositories remain independent of the Never4gA Markdown vault.
created_at: "2026-08-22T19:00:00Z"

generated:
  by: human:owner
  at: "2026-08-22T19:00:00Z"

verified:
  - by: human:owner
    at: "2026-08-22T19:00:00Z"

status: stable
authority: authoritative
lifecycle: accepted

workspace: 0198d741-0cf9-7360-b426-964081ed0a39
---
```

---

# 27. Entity Example

```yaml
---
type: entity
id: 0198d779-1a89-78a3-88f4-6fb97dfa858b
schema: never4ga/0.1
title: Python
description: The Python programming language.
created_at: "2026-08-22T19:00:00Z"

entity_type: technology

aliases:
  - Python Language

authority: informational

domains:
  - software_development

tags:
  - programming-language

resource: https://www.python.org/
---
```

Knowledge about a specific Python topic remains separate:

```text
30_Knowledge/Notes/Python Dependency Management.md
```

---

# 28. Index Example

A workspace directory may contain:

```text
workspace.md
index.md
Decisions/
Plans/
...
```

`workspace.md` carries the workspace schema.

`index.md` contains no normal concept frontmatter:

```markdown
# Sparrow

- [Workspace](workspace.md) - Canonical workspace manifest and overview.
- [Decisions](Decisions/) - Durable project decisions.
- [Plans](Plans/) - Active and historical plans.
```

This separation preserves OKF compatibility and progressive disclosure.

---

# 29. Staleness Example

```yaml
---
type: knowledge
id: 0198d795-2748-7cf5-94a8-4ebd17eea92c
schema: never4ga/0.1
title: Current Claude Code Skill Locations
description: Current filesystem locations recognized by Claude Code for skills.
created_at: "2026-08-22T19:00:00Z"

generated:
  by: human:owner
  at: "2026-08-22T19:00:00Z"

verified:
  - by: human:owner
    at: "2026-08-22T19:00:00Z"

status: stable
authority: informational
stale_after: "2026-11-22T19:00:00Z"
---
```

Once stale, the note remains available but should be flagged and reviewed before being treated as current operational truth.

---

# 30. Schema Evolution

## 30.1 Versioning

The base schema uses:

```text
never4ga/<major>.<minor>
```

During early development, `0.x` may evolve rapidly.

## 30.2 Backward-compatible changes

May include:

- new optional fields;
- new registry values;
- new optional profiles;
- new type extensions.

## 30.3 Breaking changes

Include:

- removing/renaming required fields;
- changing field meaning;
- changing ID semantics;
- changing required structural relationships.

These require a schema version bump and migration path.

## 30.4 Migration policy

Never4gA MUST NOT silently destructively migrate canonical Markdown.

Migration should:

1. detect source schema;
2. produce a plan/diff;
3. preserve unknown fields;
4. support dry-run;
5. create Git-visible changes;
6. allow rollback through normal version control.

---

# 31. Unknown Data Policy

Never4gA follows a permissive-reader philosophy.

Consumers:

- MUST preserve unknown frontmatter keys where practical;
- MUST preserve unknown `type` values;
- MUST preserve unknown registered/extension profiles;
- MUST NOT delete unknown extension data;
- SHOULD warn rather than fail when safe best-effort processing is possible.

Writers should be conservative:

> Be liberal in what you can read, strict and intentional in what you write.

---

# 32. Anti-Drift Rules

The schema enables first-class maintenance.

The future runtime SHOULD detect:

- duplicate UUIDs;
- missing required IDs;
- path/type mismatches;
- invalid registered lifecycle values;
- missing workspace targets;
- broken relation targets;
- stale content;
- deprecated content still used as current;
- superseded decisions lacking replacement links;
- AI-generated authoritative content without appropriate verification/promotion policy;
- unregistered domain proliferation;
- near-duplicate tags;
- duplicate Entities by title/alias/resource;
- relations to deleted concepts;
- orphan workspace content;
- a workspace whose required reading is larger than its ceiling (ADR-0043,
  superseding ADR-0042's per-document item);
- two decisions in one `Decisions/` folder sharing a series and number
  (ADR-0044). A gap or an out-of-order number is not a finding.

Canonical data is never silently discarded merely because maintenance finds a problem.

---

# 33. Fields Intentionally NOT Added in v0.1

To keep the schema simple, v0.1 does **not** define:

### `topics`

Use domains, tags, links, and Entity relations.

### Numeric `confidence`

Machine confidence scores are poorly portable and easy to misuse.

### Generic `modified_at`

Use OKF `generated.at` for meaningful content generation/edit provenance when reliably known; filesystem/Git timestamps remain derived operational signals.

### Relative freshness TTL

Use `stale_after`; future policies may calculate it.

### Separate source/reference taxonomy

Use OKF `sources` and Markdown links.

### Generic opaque JSON blobs at top level

Use registered fields or namespaced `extensions`.

---

# 34. Required Phase-D Follow-up

Phase D must finalize:

- workspace type registry;
- base workspace lifecycle;
- workspace profile inheritance;
- exact organization profile;
- software/product profile;
- research profile;
- event/life-project profile;
- rules for parent/child workspaces;
- workspace manifest body conventions;
- default workspace templates.

Those decisions plug into `workspace_type`, `lifecycle`, and `profiles` defined here.

---

# 35. Acceptance Tests for the Schema

A conforming implementation must be able to test at least:

## Identity

- create two concepts → IDs differ;
- move a concept → ID remains unchanged;
- duplicate ID → strict validation fails.

## OKF compatibility

- every normal canonical concept has `type`;
- `index.md` is not treated as a normal concept;
- `log.md` is not treated as an activity-log concept;
- OKF fields retain OKF meaning.

## Type placement

- Knowledge note in `Notes/` with `type: knowledge` → valid;
- decision in `Notes/` → placement error;
- registered extension can alter only explicitly extendable rules.

## Lifecycle

- `status: stable` + `lifecycle: paused` → valid;
- task lifecycle uses task vocabulary;
- decision lifecycle uses decision vocabulary.

## Provenance

- AI-generated canonical content records an agent producer;
- human verification does not erase original producer;
- source entries missing `resource` fail strict validation.

## Relationships

- relation target uses stable UUID;
- missing target is reportable without corrupting the document;
- inverse graph edges are derivable where defined.

## Extensibility

- unknown frontmatter survives round-trip;
- unknown type remains readable;
- unknown profile produces reduced-validation warning;
- namespaced extension data survives round-trip.

## Staleness

- stale concepts remain retrievable;
- stale status is computed from `stale_after`;
- stale concepts are flagged rather than silently removed.

---

# 36. Canonical Minimal Examples

## Minimal human concept

```yaml
---
type: knowledge
id: 0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31
schema: never4ga/0.1
title: Example
created_at: "2026-08-22T19:00:00Z"
---
```

## Recommended durable concept

```yaml
---
type: knowledge
id: 0198d6f2-4cb1-7a2a-8b4a-1d72ddab8f31
schema: never4ga/0.1
title: Example
description: One-sentence description optimized for humans and retrieval.
created_at: "2026-08-22T19:00:00Z"

generated:
  by: human:owner
  at: "2026-08-22T19:00:00Z"

status: stable
authority: informational

domains: []
tags: []
aliases: []
---
```

---

# 37. Summary Contract

Never4gA v0.1 concept identity is:

```text
stable UUID
+
portable Markdown
+
formal type
+
formal schema version
```

Knowledge organization is:

```text
filesystem role
+
type
+
controlled domains
+
lightweight tags
+
links
+
typed relations
```

Trust/currentness is:

```text
sources
+
generated
+
verified
+
status
+
stale_after
+
authority
+
lifecycle
```

Extensibility is:

```text
unknown types tolerated
+
profiles
+
registries
+
namespaced extensions
+
non-destructive migrations
```

This schema is intended to remain understandable without Never4gA while providing enough structure for future indexing, retrieval, memory, Obsidian Bases, maintenance, and agent context assembly.
