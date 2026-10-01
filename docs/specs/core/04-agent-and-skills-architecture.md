---
type: documentation
id: 01a03502-3e5d-72e7-beba-ab62cd23ced5
schema: never4ga/0.1
title: "Never4gA Agent & Skills Architecture v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/04_AGENT_AND_SKILLS_ARCHITECTURE.md"
---
# Never4gA Agent & Skills Architecture v0.1

**Status:** Draft for adoption  
**Phase:** E — Agent / AGENTS / Skills Architecture  
**Depends on:** Vault v0.1, Schema v0.1, Workspace v0.1  
**Primary clients:** Claude Code, Codex, Antigravity / Antigravity CLI  
**Core principle:** Normal agent clients remain normal agent clients. Never4gA supplies context and procedures; it does not proxy model inference.

---

# 1. Purpose

This specification defines how replaceable AI tools participate in one shared Never4gA system.

It answers:

- how a fresh agent session discovers Never4gA;
- how current working directory resolves to a workspace;
- how global rules differ from repo rules;
- where canonical Skills live;
- how Skills are distributed to heterogeneous clients;
- how context is assembled progressively;
- how session activity becomes shared memory;
- how agents interact with external PM/work systems;
- how all of this works without a special model gateway or wrapper.

---

# 2. Non-Negotiable Agent Principles

1. Never4gA MUST NOT proxy Claude/Codex/Antigravity model traffic.
2. A user MUST be able to launch the native client normally.
3. The agent tool remains replaceable.
4. Never4gA context must be loaded progressively.
5. Never4gA MUST NOT inject the entire vault into startup context.
6. Canonical Skills live in the vault.
7. Tool-specific Skill copies/configuration are derived adapter artifacts.
8. Never4gA MUST NOT overwrite user-owned agent configuration silently.
9. Repository-specific instructions remain owned by the repository.
10. Global personal/process standards belong in Never4gA rather than being copied manually into every repo.
11. Durable vault writes SHOULD go through validated Never4gA operations once the runtime exists.
12. Agent-derived information is not automatically promoted to authoritative knowledge.
13. External PM writes are operational side effects and require explicit authorization policy.
14. Every integration must degrade gracefully if Never4gA is unavailable.

---

# 3. Agent Surface Model

Never4gA separates five instruction/context surfaces.

```text
1. TOOL GLOBAL BOOTSTRAP
   Tiny persistent native instruction
             │
             ▼
2. NEVER4GA STARTUP SKILL
   Discovers system/workspace
             │
             ▼
3. RUNTIME CONTEXT
   Task/workspace-specific information
             │
             ├── standards
             ├── current state
             ├── relevant docs
             ├── PM state
             └── recommended skills
             │
             ▼
4. PORTABLE SKILLS
   Reusable procedural workflows
             │
             ▼
5. REPOSITORY INSTRUCTIONS
   Actual codebase-local constraints
```

These surfaces must not be collapsed into one giant `AGENTS.md`.

---

# 4. Native Launch Requirement

Never4gA MUST NOT require:

```text
never4ga launch claude
never4ga launch codex
never4ga launch agy
```

Normal use remains:

```bash
cd ~/Projects/sparrow
claude
```

or:

```bash
codex
```

or:

```bash
agy
```

A wrapper MAY be offered later for convenience, but it is not part of the architectural requirement.

---

# 5. Global Bootstrap Contract

Each supported tool receives a very small native global instruction.

Its job is only to establish the Never4gA protocol.

Conceptual instruction:

```markdown
Never4gA is the local knowledge/context system for this user.

At the beginning of a substantive session, invoke the `never4ga-startup`
Skill once before substantial project work when Never4gA is available.

Use Never4gA for durable workspace context, standards, shared memory,
and configured work-management context.

Do not treat Never4gA as a model gateway.

If Never4gA is unavailable, continue using normal repository instructions
and report that shared context is unavailable rather than blocking work.
```

The bootstrap MUST remain small.

It MUST NOT contain:

- the user's entire standards library;
- the full workspace context;
- giant project summaries;
- copied Skills;
- full PM state;
- detailed retrieval instructions.

---

# 6. Canonical Bootstrap Source

Canonical bootstrap source lives under:

```text
50_System/Agents/Bootstrap/
```

Recommended:

```text
50_System/Agents/
├── Bootstrap/
│   ├── core.md
│   └── README.md
├── Adapters/
├── Policies/
└── Documentation/
```

Tool-specific installed forms are derived.

---

# 7. Adapter Installation Strategy

Never4gA manages tool integration through adapter synchronization.

Conceptual command:

```bash
never4ga adapters sync
```

The command:

1. detects supported installed clients;
2. determines their current supported paths/capabilities;
3. computes desired generated state;
4. shows a diff/dry-run when requested;
5. writes only Never4gA-owned configuration;
6. records hashes/ownership;
7. never deletes or overwrites unknown user configuration;
8. reports conflicts.

A future background service MAY keep adapters synchronized automatically.

---

# 8. Tool-Specific Global Bootstrap

## 8.1 Claude Code

Preferred native global instruction surface:

```text
~/.claude/CLAUDE.md
```

Claude Code also discovers personal Skills under:

```text
~/.claude/skills/<skill-name>/SKILL.md
```

Never4gA SHOULD prefer adding a minimal import/reference to a generated bootstrap file when supported rather than owning the user's entire `CLAUDE.md`.

Conceptual generated source:

```text
~/.never4ga/adapters/claude-code/bootstrap.md
```

The user's global Claude file remains user-owned.

If Never4gA must modify it, it uses a small clearly delimited managed block or import line.

Never4gA MUST preserve unrelated content.

---

## 8.2 Codex

Codex global instructions live in:

```text
~/.codex/AGENTS.md
```

unless `CODEX_HOME` changes the location.

Codex user Skills are currently discoverable under:

```text
$HOME/.agents/skills/<skill-name>/SKILL.md
```

Never4gA SHOULD place only a small managed bootstrap block in the global `AGENTS.md`.

It MUST preserve the rest of the file.

Repository `AGENTS.md` files remain repo-owned and participate in Codex's native hierarchical instruction model.

---

## 8.3 Antigravity CLI

Global developer context:

```text
~/.gemini/GEMINI.md
```

Current Antigravity CLI shared Skill location:

```text
~/.gemini/antigravity-cli/skills/
```

Workspace-local Skills use:

```text
.agents/skills/
```

Antigravity also consumes workspace `GEMINI.md` and `AGENTS.md`.

Never4gA SHOULD use the global `GEMINI.md` only for the small bootstrap reference/instruction.

It MUST preserve unrelated user content.

---

# 9. Adapter Paths Are Versioned Capabilities

Tool filesystem paths are not part of Never4gA's permanent core schema.

They are adapter implementation data.

Example:

```text
adapter: antigravity_cli
adapter_version: ...
capabilities:
  global_rules_path: ...
  global_skills_path: ...
  workspace_skills_path: ...
```

Why:

Provider tools can change their discovery paths.

Never4gA should update one adapter rather than change the knowledge model.

---

# 10. Repository Instructions

Never4gA does not replace genuine repository guidance.

A repo may contain:

```text
AGENTS.md
CLAUDE.md
GEMINI.md
```

when the repository itself legitimately owns those instructions.

Examples:

- test commands;
- code layout;
- style constraints;
- build commands;
- security constraints;
- repository review policies.

Rule:

> Repository instructions describe the repository. Never4gA describes the user's durable system/context.

Never4gA MUST NOT generate redundant repo-specific instruction files merely so every vendor has an identical copy of Never4gA context.

**Amended 2026-08-30 by ADR-0025.** The rule above is unchanged and the material listed above still belongs to the repository. What changed is the *filename* it is written under.

In a mapped repository, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and every client-specific equivalent hold a **generated pointer** and are **gitignored**. The repository's own instructions — test commands, layout, style, build, security constraints, review policy — stay committed under names that are not agent instruction files: `README.md`, `CONTRIBUTING.md`, `docs/`.

This does not move repository content into the vault. A repository still describes itself, in tracked files, and a clone made by someone who has never run Never4gA can still be read and its tests still run.

One class is excepted: a repository whose agent contract is part of what it ships tracks its own `AGENTS.md`. Declared per repository in the §13 registry or the §14 marker, never hardcoded. See `details/agent-instruction-layering.md` §8.

---

# 11. Cross-Tool Repo Guidance

Because not every client natively reads the same repository instruction filename, the Never4gA startup process SHOULD report relevant repository guidance files to the current client.

Example startup output:

```text
Repository guidance:
- ./AGENTS.md
- ./docs/DEVELOPMENT.md
```

A client that did not already load those files should read them.

**Amended 2026-08-30 by ADR-0025.** The generated pointer in a mapped repository carries this list as a written artifact, so the guidance report reaches a session that never calls startup. Startup output remains the path for an unmapped repository, and the two must name the same files.

This avoids requiring:

```text
AGENTS.md
CLAUDE.md
GEMINI.md
```

to contain three manually synchronized copies of the same project rules.

---

# 12. Repository-to-Workspace Resolution

The startup process sends at minimum:

```text
cwd
client
```

The runtime resolves in this order:

1. explicit command-supplied workspace, if present;
2. local repo mapping registry;
3. optional repo marker;
4. Git root / remote identity mapping;
5. parent-directory mapping;
6. unresolved.

The user is never required to put Never4gA metadata in every Git repository.

---

# 13. Local Repo Mapping Registry

Primary personal mechanism:

```text
/path/to/repo
    ↔
Never4gA workspace UUID
```

This mapping lives in local Never4gA configuration/state, not canonical project knowledge.

Benefits:

- does not alter third-party repos;
- works with private local paths;
- works immediately;
- can map several worktrees to the same workspace.

---

# 14. Optional Repository Marker

For portable/shared cases, a repo MAY contain a small marker such as:

```text
.never4ga.yaml
```

Conceptual contents:

```yaml
workspace_id: 0198d741-0cf9-7360-b426-964081ed0a39
```

This is optional.

A marker:

- contains no secrets;
- contains no duplicate workspace documentation;
- may be committed if appropriate;
- is not required for personal operation.

Final filename/schema belongs to Phase F.

---

# 15. Startup Skill

The first mandatory portable Never4gA Skill is:

```text
never4ga-startup/
└── SKILL.md
```

Purpose:

> Resolve the current Never4gA workspace and obtain the smallest useful startup context before substantive work.

It is installed globally for all supported clients.

---

# 16. Startup Transport

Preferred order:

```text
1. Never4gA MCP tools, if available and healthy
2. Never4gA CLI thin client
3. graceful fallback
```

The CLI is the universal bootstrap fallback because coding agents reliably have shell access.

The CLI itself should eventually call the same local core API as MCP/GUI clients.

Example conceptual call:

```bash
never4ga context startup \
  --client claude-code \
  --cwd "$PWD"
```

Task-aware startup may include a short task description:

```bash
never4ga context startup \
  --client codex \
  --cwd "$PWD" \
  --task "Implement member search"
```

The full raw user prompt SHOULD NOT be persisted merely because it was used for retrieval.

---

# 17. Startup Context Manifest

A startup response should be structured and bounded.

Conceptual response:

```yaml
session_id: ...
resolved: true

workspace:
  id: ...
  title: Sparrow
  path: ...
  type: product
  lifecycle: active

parents:
  - id: ...
    title: BezaCore Labs

repository:
  root: /home/user/Projects/sparrow
  guidance:
    - AGENTS.md

work_management:
  configured: true
  provider: openproject
  project_ref: sparrow

standards:
  - id: ...
    title: Python Development Standard
    relevance: required

recommended_skills:
  - code-review
  - django-development

context:
  - id: ...
    title: Current Architecture
    reason: active workspace context

warnings:
  - ...

unresolved:
  - ...
```

The final API format is Phase F.

`relevance: required` is implemented by ADR-0043. The workspace, its parents'
workspace pages, its `Context/` documents and its newest activity log are
required reading. They are never budgeted away, and each is delivered inline
or by path.

---

# 18. Progressive Context Modes

Never4gA defines three conceptual context levels.

## `startup`

Small orientation context.

Contains:

- workspace identity;
- parent chain;
- current state;
- important standards;
- relevant repo/PM mappings;
- key warnings;
- recommended Skills.

## `focused`

Task-specific retrieval.

Example:

```text
context focus:
"implement member search"
```

May load:

- relevant requirements;
- decisions;
- architecture;
- knowledge;
- recent related activity;
- tracker work items.

## `deep`

Explicit broad investigation when necessary.

May traverse:

- larger parent context;
- broader Knowledge;
- deeper graph relationships;
- longer history.

Agents SHOULD escalate progressively rather than default to deep context.

---

# 19. Context Budget Principle

Every context response must be bounded.

Never4gA should prefer:

```text
summary + references
```

over:

```text
entire documents copied into the response
```

The agent can request details for individual context items as needed.

This produces:

```text
startup
   ↓
identify relevance
   ↓
read selected documents
   ↓
retrieve focused context
```

rather than:

```text
load 50,000 notes
```

---

# 20. Canonical Skill Library

Canonical portable Skills live in:

```text
50_System/Skills/
```

Structure follows the open Agent Skills standard:

```text
50_System/Skills/
└── code-review/
    ├── SKILL.md
    ├── scripts/
    ├── references/
    └── assets/
```

The Agent Skills `SKILL.md` format governs the Skill.

It is a declared foreign-format island under the Never4gA schema model.

Never4gA MUST NOT force normal concept frontmatter onto `SKILL.md`.

---

# 21. Skill Metadata

Use standard Agent Skills fields:

```yaml
---
name: code-review
description: Reviews code for correctness, regressions, security, and maintainability. Use when reviewing a patch or pull request.
license: ...
compatibility: ...
metadata:
  never4ga-source: authored
  never4ga-version: "0.1.0"
  never4ga-review-status: reviewed
  never4ga-exposure: global
---
```

Never4gA-specific metadata values are strings to remain compatible with the Agent Skills metadata contract.

---

# 22. Skill Exposure Classes

Never4gA adds the following management concept.

## `bootstrap`

Always installed globally.

Only a very small number of system-critical Skills.

The set, fixed by ADR-0018:

| Skill | Purpose |
|---|---|
| `never4ga-startup` | resolve the workspace and obtain the smallest useful startup context (section 15) |
| `never4ga-context` | retrieval on demand mid-session, at a stated depth -- `focused` or `deep` (section 18) |
| `never4ga-capture` | put something in `00_Inbox/` without deciding where it belongs |
| `never4ga-create` | place a concept: its type, folder, domains and frontmatter |
| `never4ga-doctor` | a health pass over the vault, and what to do about each finding |
| `never4ga-decide` | the decision ritual: draft as proposed, stop, never self-accept |
| `never4ga-checkpoint` | save session state mid-flight |
| `never4ga-wrap` | close a session: the activity log, then the tracker |

This is the same list section 32 gives, and the two were inconsistent until
ADR-0018: section 22 omitted decision-recording and health, and section 32 named
them `never4ga-record-decision` and `never4ga-maintenance`. One list now.

Eight is already stretching "a very small number". A ninth belongs in `global`
rather than here unless it is genuinely system-critical.

`never4ga-wrap` was `never4ga-closeout` until ADR-0018. `closeout` was a
drafting-stage word and `wrap` is the one in use; the rename was taken before
Milestone 7 implemented either.

A Skill name and a CLI verb need not match. `never4ga-startup` calls
`never4ga context startup`: a Skill is a unit of instruction and a verb a unit
of behaviour. They must not contradict, which is the weaker and sufficient rule.

## `global`

General-purpose user-approved Skill exposed to all supported agent clients.

Examples might eventually include:

- planning;
- research;
- code review;
- testing.

## `workspace`

Skill applies only to selected workspaces/repos.

Generated repo-local copies MAY be created in tool-native Skill locations.

## `library`

Canonical Skill exists but is not automatically exposed to every client.

It can be promoted later or explicitly invoked through Never4gA mechanisms.

This prevents an unbounded global Skill catalog.

---

# 23. Why Not Install Every Skill Globally?

Agent tools use Skill metadata during discovery.

A large library can:

- crowd discovery context;
- create ambiguous triggers;
- increase collision risk;
- reduce correct implicit Skill selection.

Therefore:

> Canonical library size may be large; exposed Skill set should be curated.

Never4gA manages exposure separately from ownership.

---

# 24. Skill Distribution

Canonical Skill:

```text
Vault/50_System/Skills/code-review/
```

Derived client copies may be:

```text
~/.claude/skills/code-review/
$HOME/.agents/skills/code-review/
~/.gemini/antigravity-cli/skills/code-review/
```

These are generated deployment artifacts.

They are not additional sources of truth.

No symlink is required.

---

# 25. Skill Synchronization Safety

The sync engine MUST maintain an ownership manifest.

For each generated destination it records:

- canonical source;
- source hash;
- deployed hash;
- adapter;
- deployment timestamp.

Rules:

1. Never overwrite an unmanaged existing Skill directory.
2. Detect name collisions.
3. Never delete an unmanaged Skill.
4. If a generated Skill was edited locally, do not destroy the edit silently.
5. Report drift and require reconciliation.
6. Removed canonical Skills may only remove copies proven to be Never4gA-managed.
7. Support dry-run.

---

# 26. Skill Name Collision Policy

If a canonical Never4gA Skill name collides with a user/repo Skill:

1. do not overwrite;
2. report collision;
3. prefer the more local repo Skill at runtime when the tool itself defines precedence;
4. allow the user to rename/alias/disable the Never4gA Skill;
5. never silently merge two Skill instruction bodies.

---

# 27. Skill Acquisition Policy

Third-party Skills are treated as source code.

Never4gA SHOULD NOT rely on one-click external installers as the canonical acquisition path.

For every adopted third-party Skill:

1. identify source repository/author;
2. inspect `SKILL.md`;
3. inspect all scripts;
4. inspect references/assets that influence behavior;
5. identify license;
6. identify network/system dependencies;
7. identify commands/scripts the Skill can run;
8. copy/vendor the approved source into the canonical Skill library;
9. retain license/attribution;
10. record origin/version/review metadata;
11. test the Skill;
12. only then expose it.

---

# 28. Skill Dependency Rule

Instruction-only Skills are preferred.

Scripts are justified when deterministic executable behavior is useful.

A Skill MUST NOT silently require:

```text
pip install ...
npm install ...
curl ... | sh
```

without the dependency being declared and approved.

Self-contained scripts are preferred.

A Skill requiring a package/tool must document:

- what it needs;
- why;
- installation mechanism;
- security implications;
- whether an alternative exists.

---

# 29. Skill Source Classes

Never4gA should track at least:

```text
authored
vendored
forked
```

## `authored`

Created locally for Never4gA/user use.

## `vendored`

Third-party Skill copied with minimal/no modification.

## `forked`

Third-party Skill intentionally modified.

Updates to vendored/forked Skills are deliberate review events, never silent remote pulls.

---

# 30. Skills vs Standards

This boundary is mandatory.

## Skill

> How do I perform a workflow?

Example:

```text
code-review
```

## Standard

> What requirements/rules govern work?

Example:

```text
Python Coding Standard
```

A Skill may retrieve/read relevant Standards.

Do not duplicate a 100-line coding standard inside every Skill.

---

# 31. Skills vs Context

Skills are procedural.

Workspace documents are contextual.

Example:

```text
Skill:
How to conduct architecture review.

Context:
Current Sparrow architecture.

Standard:
BezaCore architecture rules.
```

The startup/context system combines them at runtime.

---

# 32. The Never4gA Core Skills

Fixed by ADR-0018. Section 22 lists the same eight; this section says what each
is for.

## `never4ga-startup`

Resolve the workspace and obtain startup context.

## `never4ga-context`

Request context for the current task at a stated depth -- `focused`, or `deep`
when focused was not enough. The Skill instructs escalating progressively rather
than reaching for `deep` first (section 18).

## `never4ga-capture`

Capture new unprocessed material safely into Inbox. Capture deliberately does
*not* decide where something belongs; `never4ga-create` does.

## `never4ga-create`

Place a correctly typed concept: its type, folder, domains and frontmatter.

## `never4ga-doctor`

Run and interpret system health checks. Named for the CLI verb that ADR-0011
settled as the product's detection surface; it detects and never repairs, which
is what separates it from the Milestone 10 maintenance work.

## `never4ga-decide`

Create or propose a correctly typed Decision, following the process in the
implementation repository's `AGENTS.md`: draft as `proposed`, say plainly that it
is not in effect, stop, and only on an explicit human ruling set it `accepted`
and edit the specification.

## `never4ga-checkpoint`

Record a bounded session/activity checkpoint.

## `never4ga-wrap`

Summarize meaningful session outcomes and propose durable memory updates.

These should be small and composable.

Renamed by ADR-0018: `never4ga-closeout` to `never4ga-wrap`,
`never4ga-record-decision` to `never4ga-decide`, `never4ga-maintenance` to
`never4ga-doctor`. `never4ga-create` was added.

---

# 33. Session Lifecycle

A normal substantive agent session follows:

```text
Native agent launch
        │
        ▼
Global bootstrap
        │
        ▼
never4ga-startup
        │
        ▼
Workspace resolution
        │
        ▼
Startup context
        │
        ▼
Task-specific focused retrieval
        │
        ▼
Work
        │
   ┌────┴─────┐
   ▼          ▼
checkpoint   explicit durable actions
   │          │
   └────┬─────┘
        ▼
never4ga-wrap
        │
        ▼
episodic record + memory proposals
```

---

# 34. Startup Is Once Per Session

The global bootstrap should instruct agents to run startup once before substantive work.

Do not repeatedly run full startup on every turn.

A session may request focused context multiple times.

If CWD/workspace materially changes during the same session, re-resolution may be required.

---

# 35. Session ID

The runtime should assign a Never4gA session ID.

It is independent of:

- Claude conversation ID;
- Codex thread ID;
- Antigravity conversation ID.

Provider session IDs may be recorded as external references when available but are not canonical identity.

This permits continuity across agent providers.

---

# 36. Session Checkpoint

A checkpoint records meaningful episodic progress.

Example content:

- what was attempted;
- what changed;
- decisions proposed/made;
- unresolved problems;
- current work item;
- relevant commits/files;
- next logical step.

Checkpoints should not capture every message.

The goal is **durable continuity**, not transcript hoarding.

---

# 37. Session Wrap

`never4ga-wrap` should distinguish:

## Episodic record

What happened.

## Candidate durable knowledge

What may deserve promotion into Knowledge.

## Candidate decisions

What may deserve a Decision record.

## Workspace state changes

What Goals/Plans/Context may need updating.

For Context, ADR-0041 makes this a mechanism, not a reminder. Every wrap lists
the context documents the session's startup pack carried, as bodies or as
references, and asks whether the session changed anything they assert. A
checkpoint may declare a context document the session changed (`--context`). A
declared document whose body is unchanged since the session was given it is
reported as `outstanding_context`, beside the outstanding work below. The log
is still written. Never4gA never edits a context document itself and never
infers from age which one is wrong.

## External work changes

What PM work items may need an update.

External writes remain subject to the configured permission policy.

---

# 38. Memory Promotion Rule

Agents MUST NOT automatically promote every discovered statement to durable semantic memory.

The default flow is:

```text
observation
   ↓
episodic/checkpoint
   ↓
candidate durable memory
   ↓
policy/user verification
   ↓
canonical concept
```

Low-risk deterministic updates may later be automated by policy.

Authoritative decisions require stronger promotion rules.

---

# 39. Write Classes

Never4gA should distinguish four classes.

## Class 0 — Read

Examples:

- search;
- context;
- inspect workspace;
- list PM items.

Normally allowed within configured permissions.

## Class 1 — Provisional capture

Examples:

- Inbox capture;
- session checkpoint;
- candidate memory.

May be auto-allowed by user policy.

## Class 2 — Canonical knowledge/state change

Examples:

- accepted Decision;
- authoritative Standard;
- workspace lifecycle change;
- promoted Knowledge.

Requires explicit operation and schema validation.

## Class 3 — External side effect

Examples:

- update OpenProject ticket;
- create PM work item;
- post comment;
- trigger external action.

Requires provider authorization and explicit write policy.

---

# 40. Work Management During Agent Startup

For a work-managed workspace, startup SHOULD include only a compact PM summary.

Example:

```text
OpenProject: configured
Current in-progress items: 3
Blocked: 1
```

Do not load the entire backlog.

If the user names a work item:

```text
Work on OP #431
```

the agent should retrieve that item directly.

If no item is specified, task relevance determines whether PM search is needed.

---

# 41. Ticket-Focused Context

A ticket-focused context request may assemble:

```text
external work item
+
linked requirement
+
related decisions
+
relevant architecture
+
recent activity
+
applicable standards
+
recommended Skills
```

This is a major intended use case.

---

# 42. Tool-Independent Context Contract

The core Never4gA API should not return:

```text
claude_prompt
codex_prompt
antigravity_prompt
```

as its primary knowledge model.

It returns structured context.

Adapters format that context for their client.

This keeps:

```text
retrieval semantics
```

separate from:

```text
client presentation
```

---

# 43. MCP vs CLI

Never4gA should support both.

## MCP

Best for:

- structured tool calls;
- discoverable capabilities;
- typed agent operations;
- long-term agent integration.

## CLI

Best for:

- universal bootstrap;
- human administration;
- debugging;
- scripts;
- fallback when MCP is not configured.

Both call the same core service/library.

Neither owns canonical data independently.

---

# 44. Graceful Degradation

If Never4gA is unavailable:

1. agent continues normally;
2. repo-native instructions still apply — narrowed 2026-08-30 by ADR-0025 to mean the repository's tracked documentation (`README.md`, `CONTRIBUTING.md`, `docs/`) rather than an agent instruction file. In a working copy the generated pointer is on disk and applies too, since it does not need the service to be running. In a fresh clone it was never written and there is none;
3. agent reports shared Never4gA context unavailable if relevant;
4. no attempt is made to reconstruct global memory from guesses;
5. no normal coding/editing workflow should be blocked solely by Never4gA downtime.

---

# 45. Adapter Health / Doctor

Future commands:

```text
never4ga adapters status
never4ga adapters sync
never4ga adapters diff
never4ga adapters doctor
```

Doctor should verify:

- native bootstrap installed;
- Skill locations discoverable;
- deployed Skills match canonical hashes;
- collisions;
- stale generated files;
- Never4gA CLI reachable;
- MCP configured/healthy where applicable;
- repo mapping works.

---

# 46. Bootstrap Before Runtime Exists

Never4gA itself can be developed before Phase F is implemented.

Initial implementation repository should contain:

```text
AGENTS.md
specification/
plans/
skills/
```

A Claude Code/Codex/Antigravity session can manually read the specification package.

Temporary bootstrap instructions should say:

> Read the Never4gA specifications before implementation. The runtime context system does not exist yet; treat the provided specification files as authoritative.

As pieces come online:

```text
manual docs
   ↓
startup CLI
   ↓
workspace resolver
   ↓
retrieval
   ↓
memory
   ↓
full agent integration
```

No bootstrapping paradox exists.

---

# 47. Initial Adapter Matrix

## Claude Code

Native global:
```text
~/.claude/CLAUDE.md
```

Native personal Skills:
```text
~/.claude/skills/<skill>/SKILL.md
```

Project-specific Claude files remain repo-owned.

Never4gA:
- small global bootstrap;
- generated Skill copies;
- MCP later;
- CLI fallback.

## Codex

Native global:
```text
~/.codex/AGENTS.md
```

Native user Skills:
```text
$HOME/.agents/skills/<skill>/SKILL.md
```

Repo instructions:
```text
AGENTS.md
AGENTS.override.md
```

Never4gA:
- small managed global bootstrap;
- generated Skill copies;
- MCP later;
- CLI fallback.

## Antigravity CLI

Native global:
```text
~/.gemini/GEMINI.md
```

Native global Skills:
```text
~/.gemini/antigravity-cli/skills/
```

Workspace:
```text
AGENTS.md
GEMINI.md
.agents/skills/
```

Never4gA:
- small global bootstrap;
- generated Skill copies;
- MCP later;
- CLI fallback.

Provider paths must remain adapter configuration, not core schema constants.

---

# 48. Testing Requirements

## Bootstrap parity

Given the same CWD and task, all supported clients should resolve the same Never4gA workspace.

## Context parity

Equivalent clients should receive semantically equivalent core context.

Formatting may differ.

## Skill sync

- canonical Skill updates deploy correctly;
- unchanged Skills do not churn;
- unmanaged Skills are preserved;
- collisions are reported;
- locally modified generated copies are not silently overwritten.

## Startup budget

Startup context remains within configured size limits.

## Progressive retrieval

Large documents remain references until needed.

## Repo mapping

- repo root resolves;
- nested CWD resolves;
- worktree resolves;
- unmapped repo degrades cleanly.

## PM integration

A work-managed workspace exposes PM availability without pulling full backlog.

## Downtime

Agent can continue ordinary work when Never4gA service is unavailable.

## Provenance

Session closeout preserves agent identity and does not silently create authoritative human decisions.

---

# 49. Phase-E Decisions

1. Native agent launch remains the normal entry point.
2. No Never4gA model gateway.
3. One tiny global bootstrap per tool.
4. Startup logic lives primarily in a portable Skill.
5. Canonical Skills live in the vault.
6. Tool copies are generated, not canonical.
7. Skill distribution uses copy/sync rather than requiring symlinks.
8. Skill exposure is curated.
9. Existing third-party Skills are vendored/reviewed, not blindly installed.
10. Repo instructions remain repo-owned.
11. Never4gA may tell clients which repo guidance files to read.
12. Local repo registry is the primary personal workspace mapping mechanism.
13. Repo markers are optional.
14. MCP is preferred for structured agent calls once configured.
15. CLI is universal fallback and human/admin interface.
16. Session continuity uses Never4gA session IDs independent of provider conversations.
17. Checkpoints capture meaningful state, not full transcripts.
18. Memory promotion is deliberate.
19. PM summaries are scoped and progressive.
20. Adapters must degrade gracefully.

---

# 50. Next Phase

Proceed to:

**Phase F — Never4gA Runtime Architecture v0.1**

Phase F must turn these contracts into:

- process/service topology;
- local API;
- CLI;
- MCP;
- configuration;
- workspace resolution;
- adapter synchronization;
- indexing;
- retrieval;
- context packing;
- memory storage/promotion;
- OpenProject adapter;
- Obsidian integration;
- maintenance;
- TDD/evals;
- phased implementation plan.
