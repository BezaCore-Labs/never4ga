---
type: documentation
id: 01a03502-3e5e-7361-81cf-6f87a381d5de
schema: never4ga/0.1
title: "Never4gA Local API, CLI & MCP Contract v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/API_CLI_MCP_CONTRACT.md"
---
# Never4gA Local API, CLI & MCP Contract v0.1

---

# 1. Principle

Three interfaces expose the same application services:

```text
HTTP API
CLI
MCP
```

No interface owns separate business logic.

---

# 2. API Versioning

Local HTTP API uses:

```text
/v1/
```

Breaking API changes require a new major path/version.

The internal Python service interfaces may evolve more rapidly before v1.0.

---

# 3. Initial HTTP Capability Groups

## Health

```text
GET /v1/health
GET /v1/doctor
```

## Vault

```text
GET  /v1/vault
POST /v1/index/reconcile
POST /v1/index/rebuild
```

## Workspace

```text
POST /v1/workspaces/resolve
GET  /v1/workspaces
GET  /v1/workspaces/{id}
```

## Schema

```text
GET  /v1/schema/types      (the Type Registry -- 1163)
```

The registry is a property of the schema version rather than of a vault: the
same answer for every vault a build serves, and it needs no session. It
publishes each type's locations, required fields, lifecycle vocabulary,
optional fields with their declared kinds, and whether the generic creation
verb makes one. A surface that has it can offer the types, render the right
input per optional field and constrain a lifecycle to its legal values —
which is what body-only templates (ADR-0035 §6) stopped doing when the
placeholder block went.

## Concepts

```text
GET  /v1/concepts/{id}
POST /v1/concepts/search
POST /v1/concepts/validate
POST /v1/concepts          (create -- ADR-0035)
POST /v1/concepts/adopt    (adopt hand-written Markdown in place -- ADR-0035)
```

## Context

```text
POST /v1/context/startup
POST /v1/context/focus
POST /v1/context/deep
```

## Sessions

```text
POST /v1/sessions
POST /v1/sessions/{id}/checkpoint
POST /v1/sessions/{id}/closeout
GET  /v1/sessions/{id}
```

## Capture / canonical operations

```text
POST /v1/capture
POST /v1/decisions
```

The reservation "more generic canonical mutation endpoints may be added after
safe file-writing behavior is proven" was exercised by ADR-0035 (2026-09-07):
the two concept writes above wrap the same creation and adoption services the
CLI calls, and carry no logic of their own.

## Work management

```text
GET  /v1/work/capabilities
POST /v1/work/search
GET  /v1/work/items/{external_id}
POST /v1/work/items
PATCH /v1/work/items/{external_id}
POST /v1/work/items/{external_id}/comments
```

Write endpoints may be disabled by policy/provider capability.

## Adapters

```text
GET  /v1/adapters
POST /v1/adapters/sync
GET  /v1/adapters/diff
```

---

# 4. CLI

Initial CLI should map closely to services:

```text
never4ga init
never4ga status
never4ga doctor

never4ga validate
never4ga index
never4ga rebuild

never4ga search
never4ga context startup
never4ga context focus

never4ga workspace list
never4ga workspace show
never4ga workspace resolve
never4ga workspace create

never4ga capture
never4ga checkpoint
never4ga closeout

never4ga work list
never4ga work show

never4ga adapters status
never4ga adapters diff
never4ga adapters sync                  # dry run: clients and repositories
never4ga adapters sync --apply          # agent clients only
never4ga adapters sync --apply --repositories   # and mapped repositories (ADR-0049)

never4ga service status
never4ga service start
never4ga service stop
```

CLI output modes SHOULD include:

```text
human
json
```

JSON enables robust Skill/script integration.

---

# 5. MCP Tools

Keep the MCP surface intentionally small.

Initial read tools:

```text
never4ga_workspace_resolve
never4ga_context_startup
never4ga_context_focus
never4ga_search
never4ga_get_concept
never4ga_work_search
never4ga_work_get
never4ga_doctor
```

Initial safe/provisional write tools:

```text
never4ga_capture
never4ga_checkpoint
never4ga_closeout
```

Higher-impact writes later:

```text
never4ga_record_decision
never4ga_work_create
never4ga_work_update
never4ga_work_comment
```

Do not expose dozens of tiny storage-level MCP tools.

Expose user-meaningful operations.

---

# 6. MCP Resources

Useful read-only resources may include:

```text
never4ga://vault
never4ga://workspace/{uuid}
never4ga://concept/{uuid}
never4ga://session/{uuid}
```

Skills themselves remain filesystem-delivered Agent Skills rather than being replaced by MCP prompts.

---

# 7. MCP Prompts

Never4gA SHOULD avoid relying on MCP prompts for core procedural behavior.

Why:

Portable Skills already own reusable agent procedures.

MCP is primarily used for capabilities/data operations.

This reduces duplicate procedural systems.

---

# 8. Authentication

Local HTTP service:

- loopback only by default;
- random local bearer token;
- token stored outside vault;
- token never returned to model content.

CLI/MCP bridge retrieves credential locally.

Obsidian plugin receives a local pairing/configuration mechanism later.

---

# 9. Operation Metadata

Mutating operations should accept/derive:

```text
actor
client
session_id
workspace_id
reason
```

This supports provenance.

Clients should not be required to manufacture user identity.

The service may map the local owner to:

```text
human:owner
```

---

# 10. Dry-Run

Potentially destructive operations SHOULD support dry-run.

Examples:

```text
adapter sync
schema migration
bulk maintenance repair
project import
canonical bulk rewrite
```

Dry-run output should be machine-readable.

---

# 11. Idempotency

Where practical, operations should be idempotent.

Examples:

- adapter sync;
- indexing same unchanged file;
- repo mapping update;
- project mapping update;
- repeated closeout request with same operation ID.

External PM writes should support idempotency guards where provider capability permits.

---

# 12. Structured Errors

Errors should contain:

```text
code
message
details
retryable
repair_hint
```

Do not force agents to parse English-only stack traces.

---

# 13. Auditability

Canonical mutations should be traceable through:

- Markdown Git diff;
- generated/provenance metadata;
- session/activity log;
- service logs for operational debugging.

The runtime DB is not the sole audit record for durable content changes.
