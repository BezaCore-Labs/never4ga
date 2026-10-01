---
type: documentation
id: 01a03502-3e5f-766c-922f-90f062848c34
schema: never4ga/0.1
title: "Never4gA Data, Indexing & Maintenance Architecture v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/DATA_INDEXING_MAINTENANCE.md"
---
# Never4gA Data, Indexing & Maintenance Architecture v0.1

---

# 1. Source-of-Truth Layers

```text
CANONICAL
Markdown + frontmatter
        │
        ▼
DERIVED
SQLite indexes / cache / embeddings
        │
        ▼
EXTERNAL AUTHORITATIVE SOURCES
Repo code / PM tracker operational state
```

External sources are authoritative only for their declared responsibility.

---

# 2. Initial SQLite Database

Per-vault derived DB:

```text
index.sqlite3
```

SQLite settings SHOULD include:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
```

A suitable busy timeout should be configured.

WAL improves practical local concurrency, but the runtime should still prefer one service writer.

---

# 3. Core Tables — Conceptual

## `documents`

```text
id                  UUID string
path                vault-relative path
type
schema_version
title
description
status
authority
lifecycle
created_at
generated_at
stale_after
content_hash
file_size
filesystem_mtime
indexed_at
validation_state
```

The Markdown document remains canonical.

These columns are search/index projections.

---

## `document_metadata`

Optional projection for less-common or extension metadata.

```text
document_id
key
value_json
```

Do not force every extension into a schema migration.

---

## `domains`

```text
domain
```

## `document_domains`

```text
document_id
domain
```

## `tags`

```text
tag
```

## `document_tags`

```text
document_id
tag
```

---

# 4. Links and Relations

## `links`

Represents Markdown links.

```text
source_document_id
target_path
target_document_id
anchor
resolved
```

Paths are useful for human links.

`target_document_id` is populated when resolved.

---

## `relations`

Represents typed Never4gA relations.

```text
source_id
relation_type
target_id
```

Derived inverse relationships are not stored canonically.

The runtime may materialize/compute inverse edges for query efficiency.

---

# 5. Chunks

## `chunks`

```text
chunk_id
document_id
ordinal
heading_path
text
content_hash
start_line
end_line
```

Chunks are derived.

---

# 6. Chunking Policy

Chunk Markdown deterministically.

Preferred order:

```text
document
  ↓
heading sections
  ↓
paragraph boundaries when section is too large
```

Do not start with blind fixed-length token windows.

Each chunk carries:

- document identity;
- heading path;
- source line range;
- text hash.

This supports traceable retrieval.

Overlap should be minimal and introduced only when evaluation shows it helps.

---

# 7. Full-Text Index

Initial text retrieval uses SQLite FTS5.

Recommended indexed material:

```text
title
description
heading_path
body chunk text
aliases
selected tags
```

The runtime may weight fields differently through FTS5 BM25.

Full-text retrieval is mandatory in v0.1.

---

# 8. Vector Index

Vector retrieval is an interface, not a mandatory v0.1 dependency.

Initial:

```text
VectorIndex = disabled
```

Later implementations may include:

```text
SQLite vector extension
Qdrant
other local/remote backend
```

No vector extension is embedded into the canonical schema.

---

# 9. Embedding Provider

Embeddings are separate from vector storage.

```text
EmbeddingProvider
```

may later support:

```text
local model
Ollama-compatible service
OpenAI-compatible provider
other provider
```

No paid remote embedding provider is required.

---

# 10. Embedding Identity

Every stored embedding MUST record:

```text
model/provider identifier
dimension
chunk hash
embedding version
```

Changing model/dimension invalidates/rebuilds relevant vector data.

Embeddings are always derived.

---

# 11. Graph

V0.1 graph traversal uses SQLite `relations` + resolved Markdown links.

No Neo4j required.

Traversal supports concepts such as:

```text
parent
child
depends_on
implements
applies_to
superseded_by
related_to
links_to
```

Recursive SQL/controlled application traversal is sufficient for personal-scale v0.1.

---

# 12. File Indexing Pipeline

```text
discover file
     ↓
classify file
     ↓
ignore / foreign / concept / index / asset
     ↓
read content
     ↓
hash content
     ↓
parse frontmatter
     ↓
validate schema
     ↓
parse Markdown tokens
     ↓
extract headings/links
     ↓
build deterministic chunks
     ↓
transaction:
  update document projection
  update tags/domains
  update links/relations
  update FTS
  update optional vectors
     ↓
record index result
```

Invalid concept documents are reported rather than silently skipped.

Where safe, useful text may still be indexed with a validation warning.

---

# 13. Hashing

Use a standard cryptographic content hash such as SHA-256.

Index optimization:

```text
path + size + mtime
```

may avoid unnecessary reads, but canonical change detection is ultimately content-hash based when verification is needed.

Do not use filesystem mtime as durable semantic provenance.

---

# 14. Rename Detection

Never4gA stable UUIDs allow rename/move recognition.

When path changes but UUID remains:

```text
same concept
new path
```

The index updates path mappings without creating a new identity.

Potential duplicate UUIDs are a maintenance error.

---

# 15. File Watcher

Use filesystem events as **hints**, not the only truth.

Pipeline:

```text
filesystem event
   ↓
debounce/coalesce
   ↓
index changed paths
```

Ignore noisy/runtime locations such as:

```text
.git/
.obsidian/
runtime/cache paths
```

unless an integration explicitly opts into specific files.

---

# 16. Reconciliation

Watchers can miss events.

Therefore the service must support:

```text
startup reconciliation
manual reconciliation
periodic reconciliation
full rebuild
```

Conceptual commands:

```text
never4ga index
never4ga index --changed
never4ga rebuild
```

---

# 17. Full Rebuild Invariant

Deleting:

```text
index.sqlite3
```

and rebuilding from canonical Markdown must recover:

- document projections;
- tags/domains;
- links;
- relations;
- chunks;
- FTS;
- validation state;
- vector embeddings if embedding provider is available.

Local-only mappings/secrets are not reconstructed from the vault and are backed up separately as local configuration.

---

# 18. External Repository Indexing

Code repositories are not indexed into the core vault index in the initial build.

Why:

- coding agents already operate directly in the repository;
- code churn is high;
- source-code retrieval has different semantics;
- it is unnecessary to bootstrap the personal system.

Later:

```text
ExternalSourceAdapter: GitRepository
```

may add optional code/document indexing.

This remains derived context, never copied into canonical Knowledge by default.

---

# 19. PM Cache

External work-management records may be cached in SQLite.

Conceptual tables:

```text
external_projects
external_work_items
external_work_relations
external_sync_state
```

Cache rows record:

```text
connection
provider
external_id
provider_updated_at
fetched_at
raw_version/etag/lock_version
normalized fields
provider_extension_json
```

The external PM system remains authoritative.

---

# 20. Maintenance Findings

Use a derived table:

```text
maintenance_findings
```

Fields conceptually:

```text
finding_id
rule
severity
document_id
path
message
detected_at
resolved_at
details_json
```

Findings are derived observations.

Important unresolved findings may later be captured into canonical Logs/Decisions when appropriate.

---

# 21. First-Class Maintenance Rules

Initial rules should detect:

- invalid schema;
- duplicate UUID;
- broken Markdown link;
- broken typed relation;
- path/type mismatch;
- stale document;
- deprecated document referenced by active context;
- superseded decision missing replacement;
- unknown domain;
- near-duplicate tags;
- duplicate entity candidates;
- orphaned workspace content;
- missing workspace manifest;
- parent/child workspace mismatch;
- stale adapter deployment;
- unmapped external repo;
- unavailable configured PM connection.

---

# 22. Maintenance Scheduling

Maintenance is divided into:

## Immediate

On file/index changes:

- schema validation;
- link/relation validation;
- duplicate IDs;
- FTS update.

## Frequent lightweight

- stale deadlines;
- connection health;
- adapter drift;
- unresolved relation targets.

## Periodic heavier

- duplicate entity detection;
- taxonomy/tag cleanup suggestions;
- orphan analysis;
- broader contradiction candidates;
- full index reconciliation.

The exact cadence is configurable in Phase-F implementation rather than hardcoded into canonical files.

---

# 23. Contradiction Detection

V0.1 should not pretend contradiction detection is deterministic.

Initial deterministic contradictions:

- two concepts with same stable ID;
- accepted decision superseded but still marked accepted incorrectly;
- workspace parent mismatch;
- conflicting schema-required values.

Semantic contradiction detection using AI belongs later and produces **candidate findings**, never automatic destructive changes.

---

# 24. Doctor

Conceptual:

```text
never4ga doctor
```

reports:

```text
Vault
Schema
Index
FTS
Vector backend
Workspace mappings
Repo mappings
PM integrations
Agent adapters
Skill sync
Stale documents
Broken links/relations
Service health
```

Doctor is a core product feature, not a troubleshooting afterthought.
