---
type: documentation
id: 01a03502-3e5d-72e7-beba-ab64979f1d7a
schema: never4ga/0.1
title: "Never4gA Future Backends & Evolution Architecture v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/06_FUTURE_BACKENDS_AND_EVOLUTION.md"
---
# Never4gA Future Backends & Evolution Architecture v0.1

**Status:** Normative architectural constraint  
**Purpose:** Ensure the MVP does not create storage/retrieval lock-in

---

# 1. The Short Answer

Using SQLite first does **not** limit future semantic or graph retrieval if SQLite is treated as:

> an initial local projection backend behind stable Never4gA interfaces

and not:

> the Never4gA data model itself.

This document defines the seams that MUST exist from the first implementation.

---

# 2. Canonical vs Projection

Canonical durable truth:

```text
Markdown
+
Never4gA UUIDs
+
typed relations
+
schema metadata
```

Derived projections:

```text
SQLite metadata
SQLite FTS5
embeddings
Qdrant points
LanceDB rows
pgvector rows
Neo4j nodes/relationships
search caches
```

Every projection must be disposable/rebuildable.

---

# 3. Identity Invariant

Never4gA canonical identity is never a backend-native row/node/point ID.

Forbidden as canonical identity:

```text
SQLite INTEGER PRIMARY KEY
Neo4j internal node ID
Qdrant implementation-generated point identity
PostgreSQL serial ID
LanceDB row position
```

Stable references use Never4gA UUIDs.

Backend IDs may be derived from/map to those UUIDs internally.

---

# 4. Stable Chunk Identity

Vector/search projections need identity below the document level.

A chunk identity MUST be deterministic from stable source material.

Recommended conceptual identity inputs:

```text
document UUID
+
normalized structural locator / heading path
+
chunk ordinal within locator
+
chunk content hash
+
chunking-policy version
```

The exact encoding is implemented later.

Never4gA must be able to answer:

```text
This vector came from:
document X
heading Y
lines A-B
chunk-policy Z
content hash H
embedding model M
```

This is required for:

- re-embedding;
- backend migration;
- explainability;
- stale-vector detection.

---

# 5. Backend-Neutral Ports

Core code depends on contracts such as:

```text
MetadataIndex
TextIndex
VectorIndex
GraphIndex
EmbeddingProvider
```

not concrete storage engines.

The first implementations may be:

```text
SQLiteMetadataIndex
SQLiteFTS5Index
DisabledVectorIndex
SQLiteGraphIndex
```

but higher-level services must receive the interfaces.

---

# 6. Backend-Neutral Retrieval Candidate

Every retriever returns normalized candidates.

Conceptually:

```text
candidate_id
concept_id
chunk_id
source_path
retriever
rank
provider_score
reason
metadata
```

`provider_score` is diagnostic.

Never4gA MUST NOT assume scores from different engines are numerically comparable.

---

# 7. Fusion Above Backends

Hybrid retrieval orchestration belongs in Never4gA.

Initial fusion:

```text
lexical rank
+
vector rank
+
graph-expanded candidates
+
metadata filters
        ↓
rank fusion (e.g. RRF)
        ↓
authority / freshness / lifecycle rules
```

A provider may support native hybrid retrieval as an optimization.

Example:

Qdrant supports dense+sparse hybrid/multi-stage queries and RRF.

Never4gA may use that through the Qdrant adapter, but the public Never4gA retrieval contract remains normalized.

This allows a different backend to produce equivalent candidate sets.

---

# 8. Vector Evolution Path

## Stage V0 — No vector dependency

```text
Metadata + FTS5 + graph edges
```

Purpose:

- establish retrieval baseline;
- build eval corpus;
- validate chunking;
- avoid choosing technology prematurely.

## Stage V1 — First vector implementation

Choose after evaluation.

Candidate classes:

### Embedded local backend

Example:

```text
LanceDB
```

Benefits:

- embedded/in-process mode;
- local filesystem operation;
- vector-native storage;
- no daemon required.

### SQLite extension

Example:

```text
sqlite-vec
```

Benefits:

- minimal operational complexity;
- vectors beside SQLite-based derived data.

Caution:

At 2026-08-22 the project still describes itself as pre-v1 and expects breaking changes.

It remains a candidate, not an architectural dependency.

### Dedicated vector service

Example:

```text
Qdrant
```

Benefits:

- purpose-built vector service;
- metadata filtering;
- dense/sparse vectors;
- hybrid and multi-stage queries;
- RRF support;
- scale beyond simple embedded use.

Operational tradeoff:

- separate service/process.

### Relational + vector

Example:

```text
PostgreSQL + pgvector
```

Benefits:

- relational metadata + vectors together;
- exact nearest neighbor;
- HNSW;
- IVFFlat;
- SQL filtering/joins;
- strong future team/server path.

Operational tradeoff:

- PostgreSQL service required.

---

# 9. Vector Backend Selection Criteria

Never4gA does not choose a vector backend based on popularity alone.

Measure:

```text
retrieval quality
recall@k
MRR / nDCG where useful
latency
filtered-search quality
index build time
incremental update behavior
storage
memory
installation burden
cross-platform support
backup/rebuild complexity
API stability
maintenance health
```

Evaluate against real Never4gA retrieval cases.

---

# 10. Embedding Provider Is Separate

Never4gA MUST NOT couple:

```text
EmbeddingProvider
```

to:

```text
VectorIndex
```

Examples:

```text
local sentence-transformer → Qdrant
local model               → LanceDB
Ollama-compatible embedder→ pgvector
remote provider           → any VectorIndex
```

Each embedding records:

```text
provider/model
dimension
normalization/config
embedding schema version
source chunk hash
```

Changing model creates a new derived embedding namespace.

---

# 11. Multi-Vector Future

The VectorIndex contract should not assume one vector forever.

Future possibilities:

```text
semantic_dense
sparse_lexical
code_embedding
entity_embedding
document_summary_embedding
```

The initial implementation may expose only one vector space.

Data models must not make `embedding` a singular permanent canonical field.

---

# 12. Graph Exists From Day One

Graph architecture is **not deferred conceptually**.

Canonical graph sources already exist:

```text
typed relations in YAML
Markdown links
workspace parent/child
goal/plan/decision relationships
entity links
external references
```

SQLite v0.1 projects these into edge tables.

This is the first GraphIndex implementation, not the final graph model.

---

# 13. Graph Port

The GraphIndex contract should support conceptual operations such as:

```text
neighbors
incoming
outgoing
ancestors
descendants
traverse
shortest/reachable path when supported
filter by relation type
bounded expansion
```

The first backend may implement only a supported capability subset.

Capabilities must be discoverable.

---

# 14. SQLite Graph Stage

V0:

```text
SQLite relations table
+
resolved Markdown links
+
recursive CTE/application traversal
```

This is sufficient for:

- parent chains;
- child workspaces;
- decision supersession;
- dependency traversal;
- limited context expansion.

No graph server required.

---

# 15. Neo4j Evolution Stage

Add Neo4j when actual graph workloads justify it.

Candidate triggers:

- deep multi-hop traversal becomes common;
- graph query expressiveness matters;
- relationship-centric exploration becomes a major UI/product feature;
- graph analytics/algorithms become useful;
- SQLite traversal becomes operationally or ergonomically limiting.

Projection:

```text
Never4gA UUID → Neo4j node key
typed canonical relation → Neo4j relationship
Markdown link → projected relationship class
```

Never4gA never stores Neo4j internal IDs in canonical Markdown.

Neo4j is rebuildable from Markdown/index projections.

Current Neo4j versions also support vector indexes, but Never4gA SHOULD keep VectorIndex and GraphIndex conceptually separate even when one provider implements both.

This prevents graph vendor selection from silently becoming vector vendor selection.

---

# 16. Graph + Vector Retrieval

Future retrieval can intentionally combine:

```text
lexical candidate
       ↓
vector candidate
       ↓
graph expansion
       ↓
rerank
```

or:

```text
metadata scope
       ↓
graph neighborhood
       ↓
vector rank inside neighborhood
```

Never4gA's Context Assembler chooses strategies through a retrieval plan.

No one database owns the overall context algorithm.

---

# 17. Metadata/Relational Evolution

Personal MVP:

```text
SQLite
```

Future advanced local/server:

```text
PostgreSQL
```

Potential `PostgresMetadataIndex` provides:

- relational metadata;
- concurrency;
- server deployment;
- multi-user foundation;
- optional pgvector integration.

SQLite remains a supported personal/local profile unless there is a compelling reason to remove it.

---

# 18. Search/Text Evolution

Initial:

```text
SQLite FTS5
```

Future TextIndex adapters could include:

```text
PostgreSQL FTS
OpenSearch/Elasticsearch-class systems
other lexical engines
```

Never4gA query semantics must not expose raw FTS5 syntax as its only public API.

Provider-specific advanced query modes may be optional extensions.

---

# 19. Deployment Profiles

## Personal Core

```text
Markdown
SQLite metadata/FTS/graph
optional vector backend
```

## Advanced Local

Possible:

```text
Markdown
SQLite control/index
Qdrant
Neo4j
```

Only if real usage justifies both services.

## Integrated Server

Possible:

```text
Markdown/object storage
PostgreSQL
pgvector or Qdrant
Neo4j optional
```

## Hosted/Team

Requires a separate multi-user security/authorization architecture.

Do not build it into personal MVP.

---

# 20. Backend Migration Contract

Never4gA should eventually support operations conceptually like:

```text
never4ga backend status
never4ga backend rebuild vector
never4ga backend rebuild graph
never4ga backend migrate vector --to qdrant
never4ga backend migrate metadata --to postgres
```

Because projections are derived, the preferred operation is normally **rebuild from canonical sources**, not database-to-database transformation.

---

# 21. Capability Discovery

Backends report capabilities.

Example VectorIndex:

```text
exact_knn
ann
metadata_filtering
multiple_vectors
sparse_vectors
hybrid_native
quantization
```

Example GraphIndex:

```text
neighbors
recursive_traversal
shortest_path
graph_algorithms
vector_index
```

Core behavior checks capabilities instead of hard-coding vendor names.

---

# 22. Contract Tests

Every backend interface MUST have a reusable contract test suite.

Example:

```text
VectorIndexContract
GraphIndexContract
MetadataIndexContract
TextIndexContract
```

SQLite, Qdrant, Neo4j, LanceDB, pgvector, etc. only become supported after passing their applicable contracts.

---

# 23. No Lowest-Common-Denominator Trap

The common interface defines **portable core semantics**.

Providers may expose optional extension capabilities.

Never4gA should not cripple Qdrant or Neo4j merely because SQLite lacks a feature.

Model:

```text
portable core
+
capability discovery
+
provider extensions
```

---

# 24. Future Version Planning

## v0.x Personal foundation

- SQLite metadata/FTS/edge graph
- agent context
- OpenProject
- episodic memory
- Obsidian
- retrieval evals

## v0.x Semantic expansion

- EmbeddingProvider
- first VectorIndex
- hybrid retrieval
- reranking
- vector evals

## v0.x Graph expansion

- GraphIndex capability maturity
- graph explorer/context strategies
- optional Neo4j adapter
- graph evals

## v1 Personal stable

- stable schema/migrations
- stable CLI/API/MCP
- at least one supported semantic backend
- robust rebuild/migration
- hardened OpenProject adapter
- strong Obsidian experience

## v1+ Product/server path

- PostgreSQL adapter
- pgvector and/or Qdrant production profile
- optional Neo4j production graph profile
- multi-user security architecture if productized
- additional PM integrations

---

# 25. Architectural Tests From First Commit

The implementation must make these possible even before the backends exist:

### Test A

Replace `SQLiteTextIndex` with a fake implementation without modifying ContextAssembler.

### Test B

Run ContextAssembler with `DisabledVectorIndex`.

### Test C

Run the same retrieval eval with a fake VectorIndex.

### Test D

Project relations through SQLiteGraphIndex while canonical relations remain unchanged.

### Test E

Delete all derived backend data and rebuild from Markdown.

### Test F

No canonical document contains a backend-native primary key.

### Test G

No public API endpoint requires callers to write FTS5, Cypher, Qdrant filter syntax, or PostgreSQL SQL.

If these tests fail, the implementation is becoming welded to a backend.
