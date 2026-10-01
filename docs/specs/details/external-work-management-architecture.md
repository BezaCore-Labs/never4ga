---
type: documentation
id: 01a03502-3e5f-766c-922f-90f16de84fe4
schema: never4ga/0.1
title: "Never4gA External Work Management Architecture — v0.1 Design Note"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/EXTERNAL_WORK_MANAGEMENT_ARCHITECTURE.md"
---
# Never4gA External Work Management Architecture — v0.1 Design Note

## The key separation

Never4gA and a PM tracker solve different problems.

```text
NEVER4GA
Durable context / why / knowledge
────────────────────────────────
Workspace identity
Goals
Plans
Requirements
Architecture
Decisions
Research
Standards
Knowledge
Durable history


PM / WORK TRACKER
Operational work / what is happening now
────────────────────────────────────────
Tickets
Status
Assignees
Priority
Dates
Dependencies
Comments
Time
Milestones
```

Neither should manually duplicate the other's authoritative state.

---

## Generic architecture

```text
                 Human / Agent / Obsidian
                          │
                          ▼
                   Never4gA Core API
                          │
               Work Management Service
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        OpenProject     Jira       GitHub/Linear...
          Adapter       Adapter        Adapter
              │
              ▼
         OpenProject
```

Initially only:

```text
OpenProject Adapter
```

is implemented.

The interface is designed generically so later adapters do not change workspace semantics.

---

## Why this is not excessive complexity

We are **not** building all integrations now.

We are only:

1. reserving the concept of external work management;
2. defining the source-of-truth boundary;
3. defining a generic capability shape;
4. implementing OpenProject first.

This is materially cheaper than hard-coding OpenProject throughout the system and later having to extract it.

---

## Sync maturity ladder

### Stage 0 — Mapping only

Never4gA knows:

```text
workspace ↔ OpenProject project
```

No automatic ticket retrieval.

### Stage 1 — Read

Never4gA queries OpenProject for current operational context.

Examples:

- active work packages;
- selected ticket;
- blocked tickets;
- assigned tickets.

### Stage 2 — Cached/read-through

Never4gA maintains a derived local cache/index for faster context/search.

OpenProject remains authoritative.

### Stage 3 — Explicit writes

Agents/humans can create/update tickets through the Never4gA API.

Writes require explicit authorization policy.

### Stage 4 — Webhook-assisted synchronization

OpenProject webhooks invalidate/update relevant cached state.

Polling/manual refresh remains fallback.

---

## Import model

For each existing software project:

```text
Existing Docs ─────► Never4gA Workspace
Existing Repo ─────► External Repo Mapping
OpenProject Project ► Work Management Mapping
```

Do NOT:

```text
OpenProject tickets ─► thousands of copied Markdown task notes
```

Instead tickets remain operational records in OpenProject and are surfaced into Never4gA dynamically.

---

## Cross-references

The long-term ideal is bidirectional traceability:

```text
Requirement
   │
   └─ implemented by ─► OP #412

Decision
   │
   └─ resulted in ───► OP #417

OP #417
   │
   └─ related doc ───► Never4gA decision UUID
```

The reverse OpenProject field is optional.

Never4gA must function even when the PM tool cannot be customized.
