---
type: documentation
id: 01a03502-3e5f-766c-922f-90f34a3fcd41
schema: never4ga/0.1
title: "Never4gA OpenProject Adapter Specification v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/OPENPROJECT_ADAPTER.md"
---
# Never4gA OpenProject Adapter Specification v0.1

---

# 1. Role

OpenProject is the first implementation of the generic Work Management Provider.

Never4gA core code MUST NOT depend on OpenProject-specific concepts outside the adapter boundary.

---

# 2. Preferred Integration

The primary Never4gA OpenProject adapter SHOULD use OpenProject API v3.

Reason:

- normalized programmatic control;
- work-package read/write support;
- schema/form discovery;
- optimistic locking;
- future write support.

---

# 3. Native OpenProject MCP

Current OpenProject releases also expose:

```text
https://<openproject>/mcp
```

for AI clients.

At the time this architecture was drafted, OpenProject documents its MCP tools as **read-only**.

Never4gA may support:

```text
provider_native_mcp
```

as an optional bootstrap/passthrough mode later.

It is not the core adapter contract because:

- it is currently read-only;
- tool names/semantics are provider-specific;
- other PM providers may not expose MCP;
- Never4gA needs normalized context/cache behavior.

---

# 4. Personal Authentication

First personal implementation:

```text
OpenProject personal API token
```

stored outside the vault.

The workspace contains only:

```yaml
connection: work_openproject
provider: openproject
project_ref: ...
```

OAuth is a later product/multi-user path.

---

# 5. Read Operations — Initial

Implement:

```text
health
resolve workspace/project
list/search work packages
get work package
read work package schema
read status
read type
read priority
read assignee
read dates
read parent/relations
read versions/sprints where available
build UI URL
```

---

# 6. Write Operations — Later

Implement after read path is stable:

```text
create work package
update work package
add comment/note
```

Writes must:

- read current object first when needed;
- use current `lockVersion`;
- preserve provider validation;
- return conflict errors rather than overwrite newer changes.

---

# 7. Form/Schema Discovery

OpenProject provides forms/schemas describing allowed/required write fields.

Never4gA SHOULD use provider schema/form responses rather than hardcoding one user's OpenProject custom-field configuration.

Provider-specific custom fields remain adapter extension data.

---

# 8. Normalized Work Item

Adapter normalizes:

```text
provider
connection
project_ref
external_id
display_id
title
description
description_excerpt
type
status
priority
assignee
responsible
created_at
updated_at
start_date
due_date
percent_complete
parent
relations
version/milestone
sprint
url
provider_version
extensions
```

Unavailable fields are null/omitted.

**Amended 2026-08-31, work package 941.** `description` was added beside
`description_excerpt`, which had been the only one. The excerpt is capped at 240
characters and is what a *list* carries: a search of fifty items must not pull
fifty full descriptions across the wire to render one line each. `work get`
fetches one item deliberately, and truncating there defeats the verb — reading
941 itself, the ticket that filed this, needed the cap raised by hand and a
cache row deleted. So `get` carries the whole description and `search` carries
the excerpt, from one normalized item that holds both.

---

# 9. Caching

Read cache is derived.

Cache entry records:

```text
provider_updated_at
fetched_at
provider_version / lockVersion
normalized representation
selected raw provider extension data
```

On explicit ticket read:

- use fresh cache if policy permits;
- otherwise refresh.

Writes always operate against provider current state/version.

---

# 10. Polling First

Default personal synchronization should be:

```text
on demand
+
optional periodic refresh
```

Do not require inbound webhooks.

Why:

- local machine may sleep;
- self-hosted OpenProject may not reach a laptop-local endpoint;
- no LAN/public exposure is required.

---

# 11. Webhooks Later

OpenProject supports configured webhooks for projects, work packages, comments, time entries, and attachments with a signature secret.

Never4gA MAY add webhook-assisted cache invalidation later.

Webhook ingestion should be a separate explicitly exposed endpoint/service surface.

The core local API remains loopback-only by default.

---

# 12. Capability Discovery

OpenProject adapter reports capabilities such as:

```text
read_item
search_items
hierarchy
relations
custom_fields
comments
time_tracking
sprints
write_item
webhooks
```

Capabilities depend on:

- OpenProject version;
- configured modules;
- authenticated permissions;
- Never4gA write policy.

---

# 13. Project Migration

Importing an existing project:

```text
create Never4gA workspace
    ↓
map existing code repo
    ↓
map existing OpenProject workspace/project
    ↓
test adapter health
    ↓
retrieve representative work items
    ↓
import durable docs only
    ↓
validate links/context
    ↓
migration report
    ↓
human acceptance
```

No bulk ticket-to-Markdown migration.

---

# 14. Future Provider Adapters

The OpenProject implementation is also the reference test for generic abstraction quality.

A later Jira/Linear/GitHub adapter should be possible without altering:

- Workspace schema;
- Context Pack;
- agent startup;
- Obsidian core views;
- memory model.

If adding a second provider requires rewriting core work-management concepts, the abstraction failed.
