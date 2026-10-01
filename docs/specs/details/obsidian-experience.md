---
type: documentation
id: 01a03502-3e5f-766c-922f-90f23ba0b66a
schema: never4ga/0.1
title: "Never4gA Obsidian Experience Architecture v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/OBSIDIAN_EXPERIENCE.md"
---
# Never4gA Obsidian Experience Architecture v0.1

---

# 1. Position

Obsidian is the preferred first-class human interface for the personal system.

It is optional at the product architecture level.

Never4gA must be pleasant without requiring users to browse raw folders constantly.

---

# 2. Experience Layers

## Layer 1 — Pure Markdown

Works immediately:

```text
home.md
index.md
workspace.md
area.md
Maps
normal links
```

No plugin required.

## Layer 2 — Obsidian Bases

Provides dynamic views over frontmatter.

## Layer 3 — Never4gA Companion Plugin

Future optional plugin that talks to the local API for:

- live PM data;
- search;
- context explorer;
- system health;
- maintenance;
- runtime actions.

The plugin is not required for canonical data.

---

# 3. Initial Obsidian Pack

Milestone 1 should ship:

```text
home.md

50_System/
└── Integrations/
    └── Obsidian/
        ├── README.md
        ├── Bases/
        └── Documentation/
```

Templates remain in:

```text
50_System/Templates/
```

---

# 4. Initial Bases

Create views for at least:

```text
Active Workspaces
Life Areas
Knowledge
Entities
Goals
Plans
Decisions
Recent Activity
Stale Concepts
```

These views derive from Markdown properties.

They do not duplicate canonical information.

---

# 5. Workspace Dashboard

`workspace.md` is both semantic manifest and human workspace dashboard.

Suggested visible structure:

```text
Purpose
Current State
Goals
Plans
Recent Decisions
Requirements/Architecture when profile applies
Recent Activity
Child Workspaces
External Systems
```

Obsidian Bases may be embedded for live vault-derived sections.

---

# 6. PM Data

Obsidian Bases cannot be treated as the generic live external-PM API.

Therefore external work data has two stages.

## Initial

Workspace dashboard shows:

- PM provider/mapping;
- human link to tracker;
- possibly manually invoked Never4gA CLI/search.

## Companion Plugin

Later plugin calls local API and renders:

```text
open tickets
blocked work
assigned work
milestones
selected ticket
```

No Markdown ticket duplication.

---

# 7. Generated Data

Never4gA should avoid generating large changing Markdown caches solely to make Obsidian display external data.

Prefer the Companion Plugin/local API for live operational data.

If generated Obsidian artifacts are later required, they must:

- be clearly derived;
- live in a dedicated integration-generated location;
- be Git-ignored;
- never be indexed as canonical knowledge.

---

# 8. Obsidian Configuration

Never4gA SHOULD recommend:

```text
Use Wikilinks: off
```

so normal Markdown links are created.

The system should not require users to abandon normal Obsidian conveniences.

---

# 9. Portability

Every dashboard must retain useful plain-Markdown content when Base/plugin features are unavailable.

A user opening the vault in another Markdown editor should still understand:

- what a workspace is;
- its current state;
- its important links;
- where canonical content lives.
