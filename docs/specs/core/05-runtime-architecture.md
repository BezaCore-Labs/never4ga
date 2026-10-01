---
type: documentation
id: 01a03502-3e5d-72e7-beba-ab63618eb2d4
schema: never4ga/0.1
title: "Never4gA Runtime Architecture v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/05_RUNTIME_ARCHITECTURE.md"
---
# Never4gA Runtime Architecture v0.1

**Status:** Draft for adoption  
**Phase:** F — Runtime Architecture  
**Depends on:** Vault v0.1, Schema v0.1, Workspace v0.1, Agent & Skills v0.1

---

# 1. Purpose

This specification turns the previously defined information and agent contracts into an implementable local software architecture.

Never4gA is a **local-first context and knowledge service** around a canonical Markdown vault.

It is not:

- an LLM proxy;
- a required cloud service;
- a replacement for Obsidian;
- a replacement for source-code repositories;
- a replacement for OpenProject or other PM trackers.

---

# 2. Core Runtime Shape

Never4gA is implemented as a **modular monolith** first.

```text
                     CLIENTS

        Obsidian     CLI     Claude/Codex/Antigravity
            │         │               │
            │         │          MCP / CLI fallback
            │         │               │
            └─────────┼───────────────┘
                      ▼
              Never4gA Local API
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
    Workspace      Retrieval      Memory
    Resolver       / Context      Services
        │             │             │
        ├─────────────┼─────────────┤
        ▼             ▼             ▼
      Schema        Index       Integrations
      Engine        Engine       / Adapters
        │             │             │
        └─────────────┼─────────────┘
                      ▼
                  SQLite
                derived state
                      │
                      ▼
               Markdown Vault
              canonical state
```

A modular monolith is chosen to avoid premature distributed-system complexity.

No Redis, message broker, Qdrant, Neo4j, or separate worker service is required for v0.1.

---

# 3. Recommended Implementation Language

Never4gA v0.1 SHOULD be implemented in **Python 3.14+**.

Reasons:

- standard-library UUIDv7 support;
- strong filesystem/text processing;
- mature SQLite support;
- excellent HTTP/MCP ecosystem;
- fast iteration with coding agents;
- straightforward cross-platform packaging;
- user-readable implementation;
- suitable for CLI, service, indexing, and adapters.

If future constraints justify another implementation language, public API and Markdown contracts remain stable.

---

# 4. Initial Runtime Dependencies

Keep runtime dependencies intentionally small.

Recommended core dependencies:

```text
fastapi
uvicorn
pydantic
httpx
ruamel.yaml
markdown-it-py
watchdog
platformdirs
mcp
```

Purpose:

- `fastapi` — local HTTP API
- `uvicorn` — ASGI runtime
- `pydantic` — typed request/domain validation
- `httpx` — external integrations such as OpenProject
- `ruamel.yaml` — YAML parsing/writing with better preservation characteristics
- `markdown-it-py` — Markdown token parsing
- `watchdog` — cross-platform filesystem events
- `platformdirs` — OS-appropriate local configuration/state locations
- `mcp` — official Python MCP SDK

Use standard library where practical:

```text
sqlite3
uuid
hashlib
pathlib
tomllib
argparse
logging
asyncio
```

Do not add a dependency merely because it saves a small amount of code.

---

# 5. Proposed Repository Layout

```text
never4ga/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── specification/
├── plans/
├── skills/
├── tests/
│
└── src/
    └── never4ga/
        ├── models/
        ├── schema/
        ├── vault/
        ├── workspaces/
        ├── index/
        ├── retrieval/
        ├── context/
        ├── memory/
        ├── maintenance/
        ├── skills/
        ├── integrations/
        │   ├── work_management/
        │   └── obsidian/
        ├── adapters/
        │   ├── claude_code/
        │   ├── codex/
        │   └── antigravity/
        ├── api/
        ├── mcp/
        ├── cli/
        └── service/
```

This is not full Clean Architecture ceremony.

The goal is simply:

- core concepts do not depend on provider APIs;
- provider-specific code stays isolated;
- interfaces are replaceable;
- tests can use fake adapters.

---

# 6. Stable Vault Identity

Phase F adds one canonical concept:

```text
50_System/system.md
```

Type:

```yaml
type: system_manifest
```

Example:

```yaml
---
type: system_manifest
id: 0198e09e-5d8f-7a1f-8af8-754da3b2f524
schema: never4ga/0.1
title: Never4gA System
description: Canonical manifest for this Never4gA vault.
created_at: "2026-08-22T19:45:00Z"

authority: authoritative

profiles:
  - never4ga/core/0.1
---
```

Its UUID is the stable **vault identity**.

A moved/renamed vault remains the same vault.

This is an additive Schema v0.1 type.

---

# 7. Runtime State Lives Outside the Vault

By default, derived/local runtime state MUST live outside the Git-backed vault.

Conceptual platform locations:

```text
config/
data/
state/
cache/
```

On Linux, these should follow XDG conventions.

Conceptually:

```text
~/.config/never4ga/
~/.local/share/never4ga/
~/.local/state/never4ga/
~/.cache/never4ga/
```

Exact paths are obtained through a platform abstraction.

---

# 8. Local State Model

Per vault:

```text
data/
└── vaults/
    └── <vault-uuid>/
        ├── index.sqlite3
        └── derived/
```

State:

```text
state/
├── service/
├── logs/
└── adapters/
```

Cache:

```text
cache/
├── downloads/
└── temporary/
```

Canonical Markdown does not depend on these files.

---

# 9. Configuration Layers

Never4gA separates:

## Canonical vault configuration

Lives in Markdown/schema-governed concepts.

Examples:

- workspace profile;
- workspace work-management declaration;
- Standards;
- Skills;
- System manifest.

## Machine-local configuration

Lives outside vault.

Examples:

- vault filesystem path;
- repo-to-workspace path mappings;
- local service port;
- adapter deployment state;
- connection names/base URLs;
- secret references.

## Secrets

Never stored in committed Markdown.

Examples:

- OpenProject API token;
- OAuth refresh token;
- webhook secret;
- local API bearer token.

---

# 10. Runtime Process Model

## Development/bootstrap

Initially, commands MAY execute core functionality in-process:

```text
never4ga validate
never4ga index
never4ga search
```

This lets implementation begin before a daemon exists.

## Normal mature local operation

Run one per-user background service:

```text
never4ga serve
```

or OS-managed equivalent.

The service owns:

- filesystem watching;
- SQLite write coordination;
- HTTP API;
- cache lifecycle;
- PM cache refresh;
- maintenance scheduling.

CLI/MCP/Obsidian become clients.

---

# 11. Personal Linux Service

The first platform service adapter SHOULD support:

```text
systemd --user
```

A future command may install/manage:

```text
never4ga service install
never4ga service start
never4ga service stop
never4ga service status
```

Cross-platform service adapters for macOS/Windows are later extensions.

Core functionality cannot depend semantically on systemd.

---

# 12. Local HTTP API

The normal local service binds:

```text
127.0.0.1
```

only.

It MUST NOT bind to:

```text
0.0.0.0
```

by default.

The port is configurable.

A local bearer credential SHOULD protect mutating and sensitive endpoints even on loopback.

CORS is disabled by default.

Remote/LAN exposure is a future explicit configuration, not an implicit feature.

---

# 13. Why Local HTTP

A loopback HTTP API is selected because it is usable by:

- CLI;
- MCP bridge;
- Obsidian/Electron plugin;
- future desktop GUI;
- local scripts;
- tests.

Unix domain sockets may be supported later for local-native clients, but they should not be the sole transport because cross-platform and Electron integration matter.

---

# 14. MCP Architecture

Never4gA exposes one coherent MCP server to agents.

Recommended first transport:

```text
stdio
```

A thin executable:

```text
never4ga-mcp
```

connects to the local Never4gA API.

Benefits:

- local credential handling stays outside model context;
- works with clients that launch MCP servers as subprocesses;
- no remote MCP endpoint needs to be exposed;
- adapters configure one Never4gA server.

Later:

```text
Streamable HTTP
```

may be exposed directly.

The current official Python MCP SDK supports stdio and Streamable HTTP.

---

# 15. MCP Does Not Duplicate Business Logic

Bad:

```text
MCP implementation
  └── own search implementation

CLI implementation
  └── different search implementation
```

Correct:

```text
Core service/application logic
         │
    ┌────┼────┐
    ▼    ▼    ▼
   HTTP MCP  CLI
```

All interfaces call the same service contracts.

---

# 16. Data Write Ownership

Once the background service exists, it should be the primary writer to its derived SQLite state.

Canonical Markdown can still be edited by:

- Obsidian;
- a text editor;
- Git operations;
- Claude Code/Codex directly;
- Never4gA write operations.

The service observes/reconciles external file edits.

Never4gA MUST NOT assume it is the only Markdown writer.

---

# 17. Git Policy

Never4gA MUST NOT automatically commit the vault by default.

Git is an external durability/version-control mechanism.

Never4gA may later expose:

- Git status awareness;
- provenance from commit IDs;
- optional commit helpers.

But canonical writes should not secretly produce commits.

---

# 18. Service Startup

Service startup sequence:

```text
load local config
     ↓
resolve registered vault(s)
     ↓
validate system manifests
     ↓
open SQLite
     ↓
run DB migrations
     ↓
reconcile vault index
     ↓
start filesystem watchers
     ↓
load adapter registry
     ↓
start API/MCP-facing service
     ↓
schedule maintenance jobs
```

Any failure must produce actionable health output.

---

# 19. Failure Principle

A failed optional subsystem should not destroy the whole service.

Examples:

```text
OpenProject offline
→ vault search still works

vector backend unavailable
→ FTS retrieval still works

Obsidian not running
→ service still works

one invalid Markdown file
→ index other files; report validation error
```

---

# 20. Pluggable Port Interfaces

The implementation should define explicit Python Protocol/ABC boundaries for:

```text
DocumentStore
MetadataIndex
TextIndex
VectorIndex
GraphIndex
EmbeddingProvider
WorkManagementProvider
SecretStore
AgentAdapter
ServiceManager
```

V0.1 may have only one implementation for most ports.

Not every port is defined at once: each arrives with the milestone that first
requires it. `ServiceManager` belongs with the background service (§10, §11,
§18) and is therefore defined when that service is implemented, not before.

The interface exists so later backends do not infect core logic.

---

# 21. Initial Implementations

```text
DocumentStore
  → FileSystemMarkdownStore

MetadataIndex
  → SQLiteMetadataIndex

TextIndex
  → SQLiteFTS5Index

GraphIndex
  → SQLiteGraphIndex

VectorIndex
  → DisabledVectorIndex initially

WorkManagementProvider
  → OpenProjectProvider

SecretStore
  → OS keyring when available
  → protected local-secret-file fallback

AgentAdapter
  → ClaudeCodeAdapter
  → CodexAdapter
  → AntigravityCliAdapter
```

---

# 22. No Distributed System Yet

Do not introduce:

- Redis;
- Celery;
- Kafka;
- RabbitMQ;
- PostgreSQL server;
- Qdrant server;
- Neo4j server;
- Kubernetes.

The architecture allows later adapters, but personal v0.1 remains one local process + SQLite + Markdown.

---

# 23. Public Product Evolution

A future multi-user/server deployment may substitute:

```text
SQLite        → PostgreSQL
Local vectors → Qdrant
SQLite graph  → Neo4j/other
Local service → hosted/team service
```

without changing:

- Markdown schema;
- Workspace model;
- agent contract;
- context model;
- PM adapter interface.

That is the purpose of the port boundaries.
