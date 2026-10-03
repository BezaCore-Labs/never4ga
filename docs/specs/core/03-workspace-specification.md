---
type: documentation
id: 01a03502-3e5c-772f-ba17-2701cbb160a0
schema: never4ga/0.1
title: "Never4gA Workspace Specification v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/03_WORKSPACE_SPECIFICATION.md"
---
# Never4gA Workspace Specification v0.1

**Status:** Draft for adoption  
**Phase:** D — Workspace Specification  
**Depends on:** Vault Specification v0.1 + Phase-C compatibility correction + Formal Schema v0.1  
**Major addition:** External work-management / PM integration contract

---

# 1. Purpose

A Never4gA Workspace is a bounded context for something actively managed, pursued, built, operated, investigated, or completed.

This specification defines:

- the canonical base workspace;
- workspace lifecycle;
- parent/child behavior;
- workspace type profiles;
- standard internal semantic locations;
- workspace templates;
- external software repository mappings;
- external work-management mappings;
- source-of-truth boundaries between Never4gA and PM/tracker systems.

The key rule is:

> Every workspace has the same base semantics. Workspace profiles may add semantics, but may not relocate or redefine the base semantics.

---

# 2. Canonical Workspace Structure

A workspace directory is:

```text
<Workspace>/
├── workspace.md
├── index.md
├── Context/
├── Goals/
├── Plans/
├── Walkthroughs/
├── Tasks/
├── Decisions/
├── Research/
├── Resources/
├── Logs/
├── Assets/
├── <profile extensions>/
└── Workspaces/
```

Only `workspace.md` and `index.md` are required to exist immediately.

Other directories are created on first use.

---

# 3. `workspace.md`

`workspace.md` is the canonical semantic manifest for a workspace.

It answers:

- What is this workspace?
- What type of workspace is it?
- What is its lifecycle?
- What is its parent?
- What external systems/repos belong to it?
- What profiles govern it?
- What durable context should a human or agent know first?

Minimal example:

```yaml
---
type: workspace
id: 0198d741-0cf9-7360-b426-964081ed0a39
schema: never4ga/0.1
title: Sparrow
description: Product workspace for the Sparrow note-taking app.
created_at: "2026-08-22T19:00:00Z"

workspace_type: product
lifecycle: active

profiles:
  - never4ga/workspace/software/0.1
---
```

The Markdown body SHOULD contain:

```markdown
# Sparrow

## Purpose

## Scope

## Current State

## Key Links
```

It should remain concise. Large context belongs in `Context/`.

---

# 4. `index.md`

`index.md` is the OKF-compatible progressive-disclosure navigation file.

It contains no normal Never4gA concept frontmatter.

It SHOULD link only to useful first-hop material.

Example:

```markdown
# Sparrow

- [Workspace](workspace.md) - Canonical workspace overview and state.
- [Context](Context/) - Current orientation and constraints.
- [Goals](Goals/) - Desired outcomes.
- [Plans](Plans/) - Active and historical plans.
- [Decisions](Decisions/) - Durable decisions.
- [Research](Research/) - Workspace-specific investigations.
- [Logs](Logs/) - Activity/session history.
- [Child Workspaces](Workspaces/) - Nested bounded contexts.
```

`index.md` may be generated/maintained by Never4gA later.

---

# 5. Base Semantic Locations

## 5.1 `Context/`

Contains durable orientation/current-state material.

Examples:

- scope;
- constraints;
- current architecture summary;
- stakeholder context;
- operating assumptions;
- environment overview;
- current known limitations.

Rule:

> Context explains the workspace; it does not replace Goals, Plans, Decisions, Research, or Logs.

Recommended type:

```yaml
type: context
workspace: <workspace-id>
```

---

## 5.2 `Goals/`

Contains desired outcomes.

Recommended type:

```yaml
type: goal
workspace: <workspace-id>
lifecycle: active
```

A workspace may have many goals.

Goals are outcomes, not implementation plans.

---

## 5.3 `Plans/`

Contains intended execution.

Recommended type:

```yaml
type: plan
workspace: <workspace-id>
lifecycle: active
```

Plans may implement one or more Goals through typed relationships.

**Amended 2026-10-02 by ADR-0051.** A plan's phases each have a walkthrough in
the workspace's `Walkthroughs/` (`core/02` §21.27), recording step by step how
the phase was done.

---

## 5.4 `Tasks/`

Contains Never4gA-native task concepts only when the workspace uses Never4gA as the work-item system or when a task represents durable work that intentionally exists in the vault.

Important:

> When an external PM/tracker is configured as authoritative for work items, Never4gA MUST NOT create a second manually-maintained copy of every ticket in `Tasks/`.

In an externally tracked workspace, `Tasks/` may be empty.

Tracker work items are retrieved through the Work Management Adapter and presented in Obsidian/agent context as derived views.

---

## 5.5 `Decisions/`

All durable decisions live here regardless of workspace profile.

Examples:

- architecture decisions;
- product decisions;
- vendor decisions;
- process decisions;
- scope decisions.

A profile may add `Architecture/`, but architectural decisions still live in `Decisions/`.

---

## 5.6 `Research/`

Workspace-specific research activity.

Research answers:

> How did we investigate this?

Reusable conclusions may be promoted to `30_Knowledge/Notes/`.

---

## 5.7 `Resources/`

Workspace-specific supporting material that deserves Markdown context but does not belong in a more precise base category.

Do not use `Resources/` as a generic dumping ground.

---

## 5.8 `Logs/`

Semantic activity history.

Examples:

- AI session summaries;
- work sessions;
- meeting records;
- retrospectives;
- migration reports.

Recommended:

```yaml
type: activity_log
workspace: <workspace-id>
occurred_at: "<datetime>"
```

Never use the reserved filename `log.md`.

---

## 5.9 `Assets/`

Binary/supporting files owned primarily by this workspace.

---

## 5.10 `Workspaces/`

Contains child workspaces.

All physical workspace nesting occurs through this directory.

---

# 6. Workspace Lifecycle

Core workspace lifecycle vocabulary:

```text
proposed
active
paused
completed
cancelled
retired
```

## `proposed`

The workspace is being considered/defined but is not actively operated.

## `active`

Current work/operation is expected.

## `paused`

Temporarily inactive but expected to resume.

## `completed`

The bounded objective was achieved/completed.

## `cancelled`

The bounded objective ended without completion.

## `retired`

The workspace no longer represents active/current work but is retained for durable context/history.

Lifecycle does not automatically move the workspace to `90_Archive/`.

---

# 7. Workspace Type vs Workspace Profile

These concepts are intentionally separate.

## 7.1 `workspace_type`

Answers:

> What kind of bounded thing is this?

Initial registry:

```text
organization
product
project
initiative
research
event
personal_project
ministry
operations
```

The registry is extensible.

A workspace has exactly one primary `workspace_type`.

---

## 7.2 `profiles`

Answers:

> What structural/behavioral capabilities apply to this workspace?

Examples:

```yaml
profiles:
  - never4ga/workspace/software/0.1
  - never4ga/workspace/work_managed/0.1
```

A workspace may use multiple profiles.

This avoids creating combinatorial types such as:

```text
software_product_with_openproject_and_git
```

Instead:

```text
workspace_type: product

profiles:
  - software
  - work_managed
```

---

# 8. Base Workspace Profile

All workspaces implicitly use:

```text
never4ga/workspace/base/0.1
```

Base profile semantics are the standard directories and lifecycle defined above.

---

# 9. Organization Profile

Identifier:

```text
never4ga/workspace/organization/0.1
```

Recommended when:

```yaml
workspace_type: organization
```

Adds reserved extension folders:

```text
Strategy/
Finance/
Operations/
Brand/
```

Semantics:

## `Strategy/`

Organization-level strategic direction and positioning.

## `Finance/`

Organization-specific financial planning/context/docs.

Sensitive finance material may require future privacy/encryption policies; this profile does not define them.

## `Operations/`

Recurring operating processes/context specific to the organization.

## `Brand/`

Brand identity, messaging, brand decisions, and assets/context.

Child products/projects live under:

```text
Workspaces/
```

Example:

```text
BezaCore-Labs/
├── workspace.md
├── Strategy/
├── Finance/
├── Operations/
├── Brand/
├── Decisions/
└── Workspaces/
    ├── Never4gA/
    ├── Sparrow/
    └── bezacore.com/
```

---

# 10. Software Profile

Identifier:

```text
never4ga/workspace/software/0.1
```

Adds:

```text
Requirements/
Architecture/
Testing/
Releases/
```

## `Requirements/`

Durable product/system requirements.

Requirements are not tracker tickets.

A requirement may be implemented by one or more external work items.

## `Architecture/`

Current architectural descriptions, diagrams, design docs, and technical structure.

Durable decisions still live in `Decisions/`.

## `Testing/`

Testing strategy, quality standards, test plans, and durable testing documentation.

Individual transient test executions should normally remain in code/CI systems unless worth preserving.

## `Releases/`

Release plans, durable release notes/context, deployment/release decisions.

Source code remains external.

---

# 11. Research Profile

Identifier:

```text
never4ga/workspace/research/0.1
```

May add:

```text
Questions/
Findings/
Experiments/
```

## `Questions/`

Explicit research questions/hypotheses.

## `Findings/`

Workspace-level synthesis of findings before or instead of promotion into global Knowledge.

## `Experiments/`

Experiment designs/results where relevant.

Base `Research/` remains the place for research activity/source investigation.

---

# 12. Event Profile

Identifier:

```text
never4ga/workspace/event/0.1
```

May add:

```text
Budget/
Vendors/
Schedule/
Attendees/
```

Use only when useful.

---

# 13. Work-Managed Profile

Identifier:

```text
never4ga/workspace/work_managed/0.1
```

This profile allows a workspace to use an external project-management/work-tracking system.

Examples of future providers may include:

- OpenProject;
- GitHub Issues/Projects;
- Jira;
- Linear;
- Azure DevOps;
- other systems implementing the adapter contract.

Never4gA itself does not become the mandatory ticket tracker.

---

# 14. Work Management Source-of-Truth Boundary

For a work-managed workspace, canonical responsibility is split deliberately.

## Never4gA is authoritative for

- workspace identity/context;
- durable documentation;
- goals;
- durable plans;
- requirements;
- architecture;
- decisions;
- research;
- standards;
- reusable knowledge;
- semantic relationships;
- durable session/history summaries;
- mapping configuration.

## External PM/tracker is authoritative for operational work-item state

By default:

- ticket/work-item ID;
- subject/title;
- tracker type;
- tracker status;
- assignee;
- priority;
- start/due dates;
- percent complete;
- parent/child work-item relationships;
- work-item dependencies;
- operational comments;
- time tracking;
- sprint/version/milestone membership where supported.

This boundary prevents two manually-maintained sources of truth.

---

# 15. Workspace Work-Management Declaration

The `work_managed` profile defines a top-level `work_management` field in `workspace.md`.

Example:

```yaml
profiles:
  - never4ga/workspace/software/0.1
  - never4ga/workspace/work_managed/0.1

work_management:
  mode: external
  connection: work_openproject
  provider: openproject
  project_ref: sparrow
  sync_policy: reference
```

## Fields

### `mode`

V0.1 values:

```text
none
native
external
```

`native` means Never4gA `Tasks/` is authoritative.

`external` means an adapter-backed PM system is authoritative for operational work items.

### `connection`

References a named local integration connection.

It MUST NOT contain credentials.

### `provider`

Adapter identifier.

Examples:

```text
openproject
github
jira
linear
```

### `project_ref`

Provider-specific project identifier.

It must be treated as an opaque string by the workspace layer.

### `sync_policy`

Initial values:

```text
reference
read
read_write
```

#### `reference`

Never4gA knows the project mapping but does not automatically ingest/synchronize ticket data.

Agents/humans may follow links or explicitly request tracker context.

#### `read`

Never4gA may query tracker work items and build derived views/context.

Tracker remains authoritative.

#### `read_write`

Never4gA may also create/update tracker work items when explicitly authorized.

The tracker remains authoritative for work-item state.

For the first implementation, `read` SHOULD precede `read_write`.

---

# 16. No Secrets in the Vault

The vault may contain:

```yaml
connection: work_openproject
```

but MUST NOT contain:

```yaml
api_token: secret...
password: ...
oauth_refresh_token: ...
```

Connection configuration is split:

## Portable/canonical configuration

May define:

- connection name;
- provider type;
- non-secret behavior;
- optional base URL if safe to share;
- adapter policy.

## Local secret/runtime configuration

Contains:

- API tokens;
- client secrets;
- OAuth refresh tokens;
- webhook secrets.

Local secrets belong outside committed Markdown, using a future mechanism such as:

- OS keyring;
- environment variables;
- local ignored configuration.

Exact storage belongs to Phase F.

---

# 17. External Work-Item Identity

Never4gA does not assign Never4gA UUIDs to every external ticket by default.

An external work item is identified by:

```text
connection + project_ref + external_id
```

Example conceptual identity:

```text
work_openproject / sparrow / 1234
```

Never4gA may derive an internal cache key, but the provider ID remains authoritative.

This avoids creating thousands of duplicate canonical Markdown task concepts.

---

# 18. Linking Never4gA Concepts to External Work Items

Concept documents may need to reference operational work items.

Phase D introduces an extension relationship reference form conceptually represented as:

```yaml
external_refs:
  - system: work_management
    connection: work_openproject
    project_ref: sparrow
    id: "1234"
```

This field SHOULD be formalized as a registered profile field before implementation.

Examples:

A requirement may be implemented by:

```text
OpenProject #1234
OpenProject #1235
```

A decision may have resulted in:

```text
OpenProject #1250
```

Never4gA should render these as human-usable links when adapter configuration is available.

---

# 19. Optional Reciprocal Identity in PM Tools

Never4gA MUST work without modifying the external PM schema.

However, providers that support custom fields MAY optionally store Never4gA references.

Examples:

- Never4gA workspace ID on a project;
- Never4gA concept ID on a work item;
- canonical document URL/path.

This is optional optimization, not a core dependency.

The adapter must remain usable when no custom fields can be added.

---

# 20. Derived Work Views

When an external tracker is authoritative, Never4gA may create **derived views**, not duplicated canonical tickets.

Examples in Obsidian:

- My open Sparrow tickets;
- Current milestone;
- Blocked work;
- Recently updated tickets;
- Tickets related to this requirement.

These may be backed by:

- live adapter queries;
- cache/index tables;
- generated transient views.

They SHOULD NOT become manually edited Markdown copies of every external work item.

---

# 21. Agent Behavior in Work-Managed Workspaces

At workspace startup, context assembly should determine whether a workspace has `work_management`.

Example agent startup:

```text
cwd: ~/Projects/sparrow
         ↓
resolve workspace: Sparrow
         ↓
load workspace.md
         ↓
discover:
  provider = openproject
  connection = work_openproject
  project_ref = sparrow
         ↓
retrieve:
  relevant durable vault context
  +
  relevant current work-item context
```

The agent should not need to know OpenProject-specific API semantics.

It calls generic Never4gA work-management capabilities.

Examples conceptually:

```text
work.list
work.get
work.search
work.create
work.update
work.comment
```

Provider adapters translate these operations.

The exact API belongs to Phase F.

---

# 22. Safe Write Behavior

Writes to an external tracker are operational side effects.

Never4gA MUST distinguish:

- read operations;
- draft/proposed writes;
- actual writes.

V0.1 architecture should support a policy such as:

```text
read freely within authorization
write only when explicitly requested/authorized
```

Provider concurrency/version mechanisms must be honored.

Never4gA must not overwrite newer tracker state using stale cached data.

---

# 23. OpenProject First Adapter Requirements

OpenProject is the first intended work-management adapter.

The adapter should eventually support, at minimum:

## Read

- resolve project;
- list/filter work packages;
- read work package;
- read status/type/priority/assignee/dates;
- read parent/child and relations where exposed;
- read relevant custom fields/schema;
- construct human links to the OpenProject UI.

## Write — later stage

- create work package;
- update work package;
- add comment/note;
- update supported fields;
- respect optimistic locking/version requirements.

## Event-driven maintenance — later stage

Support OpenProject webhooks for relevant project/work-package events when configured.

Webhook support is an optimization.

Polling/manual refresh must remain a valid fallback.

---

# 24. Generic Work Management Adapter Contract — Phase-D Requirements

Every future provider adapter must normalize at least:

## Provider project

```text
external_id
name
url
status (if available)
parent (if available)
```

## Work item

```text
external_id
project_ref
title
type
status
priority
assignee
created_at
updated_at
start_date
due_date
parent
url
```

Additional provider-specific fields may be exposed as extension data.

Adapters MUST NOT force all providers into false equivalence.

If a provider lacks a feature, the capability is reported unavailable.

---

# 25. Capabilities

The future adapter contract should expose provider capabilities such as:

```text
list_items
read_item
create_item
update_item
comment
relations
hierarchy
custom_fields
webhooks
time_tracking
milestones
```

An agent/UI must check capability availability rather than assume every tracker behaves like OpenProject.

---

# 26. Multiple Work-Management Systems

A workspace SHOULD use one primary authoritative work-management connection in v0.1.

Supporting multiple simultaneous authoritative trackers creates conflict and is intentionally deferred.

Future support may allow secondary read-only sources.

If a project migrates trackers:

1. old tracker mapping becomes historical/read-only;
2. new tracker becomes primary;
3. crosswalk/migration records are stored;
4. Never4gA does not pretend both are simultaneously authoritative.

---

# 27. Existing Project Import Strategy

Existing projects should be imported one at a time.

Recommended sequence:

## Step 1 — Create the Never4gA workspace

Create:

```text
workspace.md
index.md
```

and appropriate profile folders.

## Step 2 — Register external code repo

Map the real source repository.

Do not copy source into the vault.

## Step 3 — Register PM project

Example:

```yaml
work_management:
  mode: external
  connection: work_openproject
  provider: openproject
  project_ref: <existing-project-id-or-identifier>
  sync_policy: read
```

## Step 4 — Import durable documentation

Classify existing project documentation into:

- Context;
- Goals;
- Plans;
- Decisions;
- Requirements;
- Architecture;
- Research;
- Resources;
- Logs.

Do not import ticket state as duplicate Markdown tasks.

## Step 5 — Validate relationships

Confirm:

- repo ↔ workspace;
- PM project ↔ workspace;
- parent workspace;
- requirements/decisions/docs;
- PM access.

## Step 6 — Produce migration report

Record:

- imported docs;
- duplicates;
- unresolved docs;
- missing metadata;
- stale docs;
- PM mapping;
- repo mapping;
- validation errors.

Store as an activity/migration log.

## Step 7 — Human verification

Only after review mark the workspace migration as accepted/current.

## Step 8 — Move to next project

Do not bulk-import the whole legacy system first.

---

# 28. Example — BezaCore + Sparrow

```text
10_Workspaces/
└── BezaCore-Labs/
    ├── workspace.md
    ├── Strategy/
    ├── Operations/
    └── Workspaces/
        └── Sparrow/
            ├── workspace.md
            ├── index.md
            ├── Context/
            ├── Goals/
            ├── Plans/
            ├── Decisions/
            ├── Research/
            ├── Requirements/
            ├── Architecture/
            ├── Testing/
            ├── Releases/
            └── Logs/
```

`Sparrow/workspace.md`:

```yaml
---
type: workspace
id: 0198d741-0cf9-7360-b426-964081ed0a39
schema: never4ga/0.1
title: Sparrow
created_at: "2026-08-22T19:00:00Z"

workspace_type: product
lifecycle: active
parent: <bezacore-workspace-id>

profiles:
  - never4ga/workspace/software/0.1
  - never4ga/workspace/work_managed/0.1

work_management:
  mode: external
  connection: work_openproject
  provider: openproject
  project_ref: sparrow
  sync_policy: read
---
```

The source repository may separately map:

```text
~/Projects/sparrow
    ↔
workspace ID 0198d741-...
```

---

# 29. Workspace Creation

A future command such as:

```text
never4ga workspace create
```

should conceptually ask/select:

1. title;
2. workspace type;
3. parent workspace;
4. profiles;
5. optional code repositories;
6. optional work-management system.

It should then create only required files/directories and selected profile structure.

It MUST NOT create a giant tree of empty folders merely because they exist logically.

---

# 30. Child Workspace Rule

Create a child workspace when the candidate context has at least several of these properties:

- independent purpose;
- independent lifecycle;
- its own goals;
- its own plans;
- its own decisions;
- its own work items;
- independent stakeholders;
- separate repository;
- separate PM project;
- enough context to benefit from separate startup/context assembly.

Do not create a workspace merely because something is a topic or folder category.

**Amended 2026-08-30 by ADR-0024.** A workspace's parent may be a life area. A
bounded pursuit that meets the test above and belongs to a responsibility rather
than to a project — a course, a certification — lives under
`20_Life/<Area>/Workspaces/`, names the area as `parent`, and works exactly as a
child workspace under `10_Workspaces/` does. The area itself is never a
workspace: it is carried, not pursued, and does not finish.

---

# 31. Parent/Child Inheritance

Parent context is not automatically copied into child workspaces.

Context assembly may inherit selected parent information dynamically.

Examples:

A Sparrow agent may need:

- Sparrow workspace;
- BezaCore standards/strategy relevant to Sparrow;
- global Never4gA procedural standards.

The Markdown files remain stored once.

Phase E/F define context inheritance and retrieval.

---

# 32. Workspace Standards Inheritance

A workspace profile may declare applicable Standards but does not duplicate them.

Example:

```text
50_System/Standards/Software-Development/
```

applies to many software workspaces.

A child workspace may override a standard only through an explicit, documented exception mechanism defined later.

---

# 33. Obsidian Workspace Experience

For the personal installation, a workspace dashboard should provide a human-friendly view combining canonical and derived information.

Example:

```text
SPARROW

Purpose / Current State
Goals
Active Plans
Recent Decisions
Requirements
Architecture
Recent Logs

WORK
Open tickets        ← derived from OpenProject
Blocked tickets     ← derived from OpenProject
Current milestone   ← derived from OpenProject

CONTEXT
Parent: BezaCore Labs
Repo: ~/Projects/sparrow
PM: OpenProject
```

OpenProject data should appear as dynamic/derived operational context rather than duplicate Markdown tickets.

---

# 34. Phase-D Decisions

## D-001

All workspaces share one stable base semantic structure.

## D-002

Only `workspace.md` and `index.md` are immediately required.

## D-003

Workspace type and workspace profile are separate.

## D-004

Workspaces may have multiple additive profiles.

## D-005

External PM/work trackers are first-class optional operational systems.

## D-006

Never4gA is not itself required to be the authoritative ticket tracker.

## D-007

When an external tracker is authoritative, `Tasks/` does not mirror every tracker work item.

## D-008

PM integration is provider-neutral at the core architecture level.

## D-009

OpenProject is the first concrete PM adapter.

## D-010

Only the OpenProject adapter needs to be implemented initially.

## D-011

Tracker credentials never live in committed canonical Markdown.

## D-012

External work-item operational state remains authoritative in the PM tool.

## D-013

Never4gA remains authoritative for durable context/documentation/decisions/knowledge.

## D-014

Provider custom fields may improve reciprocal identity but are optional.

## D-015

Existing projects are migrated one at a time with validation/reporting.

## D-016

Read-only PM integration should precede write-enabled integration.

---

# 35. Deferred to Phase E

Agent/Skills architecture must define:

- startup skill;
- workspace discovery;
- how agents learn PM capabilities;
- when PM context is loaded;
- ticket-focused session startup;
- session closeout;
- references between tickets and durable decisions/docs;
- portable skills for PM workflows.

---

# 36. Deferred to Phase F

Runtime architecture must define:

- generic Work Management Adapter interface;
- OpenProject adapter implementation;
- connection registry;
- secret storage;
- API token/OAuth handling;
- caching;
- webhook endpoint/security;
- polling fallback;
- write permissions/confirmation;
- concurrency/optimistic locking;
- provider capability discovery;
- derived Obsidian PM views;
- external reference indexing;
- migration tooling;
- offline behavior.

---

# 37. Acceptance Criteria

Phase D succeeds if:

1. every workspace uses predictable base semantic locations;
2. workspace profiles add rather than redefine base structure;
3. BezaCore can contain child workspaces such as Sparrow;
4. code repositories remain external;
5. a workspace can map to an external PM project;
6. PM tickets do not need duplicate manually-maintained Markdown task files;
7. OpenProject can be added first without coupling the core architecture to OpenProject;
8. future Jira/Linear/GitHub/etc. adapters can use the same abstract role;
9. secrets remain outside the committed vault;
10. an agent launched in an external code repo can eventually resolve both durable vault context and current tracker work;
11. existing projects can be migrated incrementally and validated one at a time.
