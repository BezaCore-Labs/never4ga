---
type: documentation
id: 01a03502-3e5b-75de-8d71-9dd899bcb4fd
schema: never4ga/0.1
title: "Never4gA Vault Specification v0.2 — Consolidated"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/01_VAULT_SPECIFICATION.md"
---
# Never4gA Vault Specification v0.2 — Consolidated

**Status:** Normative for implementation  
**Supersedes:** Phase-B v0.1 where inconsistent with the Phase-C OKF correction

## 1. Canonical root

```text
/
├── index.md
├── home.md
├── 00_Inbox/
├── 10_Workspaces/
├── 20_Life/
├── 30_Knowledge/
├── 50_System/
└── 90_Archive/
```

The six conceptual roots are intentionally stable.

There were seven until 2026-09-05. **ADR-0032 dissolved `40_Entities/`**: an
entity record turned out to be a knowledge note in a second place — a
`LINK_RELATION` edge does not read the target's type, `aliases` resolve on any
document, and the context assembler already grouped `entity` with `knowledge`
and `resource`. Removing a root is the same weight as inventing one, which is
why it took a decision.

Extensions normally occur inside those roots rather than by inventing new top-level topical roots.

A directory that `never4ga init` found already present and registered as
**foreign material** (ADR-0039) is not a topical root and does not count
against this rule. It is the user's, held beside the roots untouched, indexed
by path for search only, and emptied one document at a time as its contents
are adopted into their typed homes. The registration is recorded in
`50_System/system.md`.

**Amended 2026-10-01 under ADR-0039.** A loose Markdown note `init` finds at
the top level is registered as foreign material the same way, by name, and is
held, indexed by path and adopted exactly as a note in a registered directory
is. `index.md`, `home.md` and `log.md` at the vault root are Never4gA's and are
never registered.

## 2. Root `index.md`

The root `index.md` is the OKF bundle/index entry point.

It may declare:

```yaml
---
okf_version: "0.2"
---
```

It is navigation, not an ordinary Never4gA concept.

## 3. `home.md`

`home.md` is the human-facing dashboard.

It is a normal Never4gA concept and may use Obsidian-specific enhancements while retaining useful plain Markdown.

## 4. `index.md` rule

Inside directories, `index.md` is reserved for progressive-disclosure/navigation and MUST NOT be used as the semantic manifest carrying ordinary Never4gA concept frontmatter.

Semantic containers use separate manifest concepts.

### Workspace

```text
<Workspace>/
├── workspace.md
├── index.md
└── ...
```

### Life area

```text
<Life Area>/
├── area.md
├── index.md
└── ...
```

## 5. Inbox

```text
00_Inbox/
├── index.md
└── Assets/     # optional
```

Inbox is temporary/unprocessed.

It remains shallow.

## 6. Workspaces

A workspace is a bounded context actively managed or pursued.

Canonical base semantics:

```text
<Workspace>/
├── workspace.md
├── index.md
├── Context/
├── Goals/
├── Plans/
├── Tasks/
├── Decisions/
├── Research/
├── Resources/
├── Logs/
├── Assets/
├── <profile extensions>/
└── Workspaces/
```

Only the files/directories actually needed must exist.

If a semantic concept is used, it MUST use its standardized location.

All child workspaces physically live under `Workspaces/`.

Software source repositories remain outside the vault.

## 7. Life

```text
20_Life/
├── index.md
└── <Life Area>/
    ├── area.md
    ├── index.md
    ├── ... (working material, grouped as the practice itself is structured)
    └── Workspaces/          (bounded pursuits belonging to this area)
```

Life areas are long-lived responsibilities.

**Amended 2026-08-30 by ADR-0024.** An area holds its own documents: `resource`,
`activity_log`, `plan`, `goal`, `task`, `research_note` and `decision` may live
under `20_Life/<Area>/`, with the same "extend inside on first use" allowance a
workspace has. Grouping inside an area is allowed when it mirrors the structure
of the thing being practised — `gardening/seed-catalog/`, `reading/classics/war-and-peace/`
— and this does not contradict §8: that section forbids an invented taxonomy in
`30_Knowledge/Notes/`, not the inherent structure of a practice.

An area may also contain workspaces. `20_Life/<Area>/Workspaces/` works exactly
as a workspace's own `Workspaces/` does, and a workspace there names its area as
`parent`. A course with units and a final, a certification with an exam date —
these meet `core/03` §30's test and are workspaces that happen not to be
software; the area itself stays the ongoing responsibility.

The line between area material and knowledge is *practice versus reference*:
reference you would consult from any context belongs in `30_Knowledge/Notes/`;
the practice of a responsibility — a study guide for unit 6, a season's
planting plan — means what it means inside that responsibility and stays in the
area. `knowledge` gains no `20_Life` location.

Software projects related to a Life area still live in `10_Workspaces/` and are
linked semantically.

## 8. Knowledge

```text
30_Knowledge/
├── index.md
├── Notes/
├── Maps/
└── Assets/
```

`Notes/` remains semantically flat.

Never create a long-lived topical folder hierarchy such as:

```text
Programming/Python/
Theology/
Home/
```

Semantic organization uses:

- type;
- domains;
- tags;
- Markdown links;
- typed relations;
- Maps;
- derived indexes.

Research is primarily an activity in a Workspace.

Sources are primarily provenance, not a permanent folder taxonomy.

## 9. Entities — retired

**Removed 2026-09-05 by ADR-0032.** `40_Entities/` held one document per
canonical thing, typed by an `entity_type` field. Measured on 120 records:
117 of their 118 resolved inbound links came from other entity records and
exactly one from the rest of the vault, while a single curated `map` in
`30_Knowledge/Maps/` gave 114 of 118 knowledge notes an inbound edge.

The three things the root claimed to provide were each already true of any
document, so what it actually produced was a second place to look and a fight
over filenames — `30_Knowledge/Notes/docker.md` and a `docker` entity record
answer to one name, and a bare wikilink then reaches neither.

Where its contents went: a thing you write *about* is a note in
`30_Knowledge/Notes/`; a machine or a service belongs to the workspace that
operates it; and a **person** is `core/02` §21.23's own type, living in
`20_Life/<Area>/`. The section number is retired rather than reused.

**What survives unchanged is the rule this section existed to state**, and it
now applies to the areas instead: kind is metadata, and no root grows a folder
per kind of thing.

## 10. System

```text
50_System/
├── index.md
├── system.md
├── Standards/
├── Schemas/
├── Templates/
├── Skills/
├── Agents/
├── Documentation/
├── Integrations/
└── <future stable system roles>/
```

`system.md` is the stable vault/system manifest.

System is extensible by stable semantic responsibility.

Adding `Tools/` later is valid when documented.

## 11. Archive

Archiving is status-first.

Completion does not automatically move files.

`90_Archive/` is for intentionally detached historical material.

```text
90_Archive/
├── index.md
├── Workspaces/
├── Life/
├── Knowledge/
├── Entities/
└── System/
```

Create subfolders only when used.

## 12. Assets

Binary/supporting files use nearest-owner `Assets/`.

Durable facts should not exist only in opaque binary files when practical.

## 13. Reserved `log.md`

OKF `log.md` is reserved directory history.

Never4gA workspace semantic logs live under:

```text
Logs/<YYYY>/*.md
```

with `type: activity_log`. The year directory is created when its first record
is written; filenames keep a full date prefix, so a record names its own year
and the path is never the only thing carrying it.

Do not name an activity record `log.md`.

When a year closes, its records are rolled up: a condensed
`Logs/<YYYY>_summary.md` stays in the workspace, and the originals move intact
to `90_Archive/Workspaces/<Name>/Logs/<YYYY>/`. Nothing performs this
automatically; it is a maintenance operation run on request, and the originals
are never edited or deleted (section 32 of core/02).

A workspace's Recent Activity view must therefore bound by date or path rather
than by `workspace` alone, or a rolled-up year reappears in the dashboard it
just left.

Added by ADR-0016.

## 14. Portability

Canonical facts remain Markdown/frontmatter.

Obsidian Bases, plugin state, SQLite, vectors, graph projections, and caches never become the only representation of durable knowledge.

## 15. Ownership decision table

| Question | Destination |
|---|---|
| Unprocessed? | `00_Inbox/` |
| Bounded active context? | `10_Workspaces/` |
| Ongoing area of life? | `20_Life/` |
| Durable reusable knowledge? | `30_Knowledge/Notes/` |
| Curated knowledge navigation? | `30_Knowledge/Maps/` |
| A person? | `20_Life/<Area>/` |
| Governs Never4gA/workflows? | `50_System/` |
| Intentionally detached historical material? | `90_Archive/` |
