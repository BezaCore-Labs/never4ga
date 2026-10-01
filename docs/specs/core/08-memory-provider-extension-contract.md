---
type: documentation
id: 01a03502-3e5e-7361-81cf-6f83e345653d
schema: never4ga/0.1
title: "Never4gA Memory Provider & Augmentor Contract v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/08_MEMORY_PROVIDER_EXTENSION_CONTRACT.md"
---
# Never4gA Memory Provider & Augmentor Contract v0.1

**Status:** Normative extension architecture  
**Purpose:** Permit Mem0, Graphiti, Zep, and future memory systems without outsourcing Never4gA's canonical memory.

---

# 1. Canonical Memory Model Remains

Never4gA's core memory model is:

```text
Working     → local runtime state
Episodic    → Workspace/Logs/
Semantic    → canonical Markdown concepts
Procedural  → Skills + Standards
```

External memory systems do not replace this model.

---

# 2. External Memory Principle

An external memory system is, by default:

```text
augmentation
projection
candidate source
```

not:

```text
canonical durable truth
```

Important durable information must be promotable into canonical Never4gA Markdown.

---

# 3. Why This Matters

External systems can:

- change APIs;
- change licensing/hosting;
- be discontinued;
- alter extraction models;
- lose/rebuild derived graph/vector state;
- produce inferred facts.

Never4gA must remain useful without them.

---

# 4. Two Extension Roles

Do not force every memory product into one false abstraction.

## `MemoryAugmentor`

Provides relevant memory/context candidates.

Examples:

```text
Mem0
Zep user memory/context
future memory service
```

Conceptual operations:

```text
search
retrieve_context
ingest_episode
delete_projection
health
capabilities
```

## `TemporalGraphProvider`

Provides evolving facts/entities/relationships with temporal semantics.

Examples:

```text
Graphiti
Zep Context Graph
future temporal graph engine
```

Conceptual operations:

```text
ingest
search
neighbors
facts
history
valid_at
invalidate/rebuild projection
health
capabilities
```

One provider may implement both roles.

---

# 5. Native Provider

Never4gA always has:

```text
NativeMemoryProvider
```

which is simply the canonical Never4gA memory model plus native indexes.

External providers are optional.

---

# 6. Memory Candidate Contract

External memory retrieval returns normalized candidates.

Conceptually:

```text
provider
external_memory_id
text/fact
entities
source/session references
valid_from
valid_to
created_at
updated_at
provider_score
provenance
confidence if provider supplies it
```

Provider confidence remains provider metadata.

Never4gA does not convert it automatically into canonical authority.

---

# 7. Canonical Promotion

External memory may result in:

```text
context-only candidate
episodic reference
candidate knowledge
candidate decision
candidate entity update
```

Promotion flow:

```text
external memory result
       ↓
Never4gA candidate
       ↓
schema/provenance check
       ↓
policy/human verification as required
       ↓
canonical Markdown
```

Once promoted, the Markdown UUID is authoritative.

---

# 8. External Ingestion Policy

Never4gA may send selected material to an external memory provider.

Do NOT blindly stream every private vault file or every full transcript by default.

Ingestion scopes should be configurable:

```text
session checkpoints
activity logs
specific workspace concepts
selected Knowledge
selected Entities
explicit user capture
```

Privacy/hosting characteristics matter.

---

# 9. Session Ingestion

Preferred default for external memory:

```text
Never4gA checkpoint / closeout
```

rather than every raw chat turn.

Why:

- reduces noise;
- reduces volume;
- preserves curated episodic semantics;
- remains provider-independent;
- avoids transcript hoarding.

A provider-specific mode may support raw turn ingestion when intentionally configured.

---

# 10. Mem0 Adapter

Mem0 may implement `MemoryAugmentor`.

Potential uses:

```text
ingest session/checkpoint memories
retrieve relevant prior memories
surface entity-linked memories
```

Never4gA must not assume Mem0's storage/graph implementation remains constant.

Mem0-specific APIs stay inside the adapter.

---

# 11. Graphiti Adapter

Graphiti is a strong candidate `TemporalGraphProvider`.

Potential uses:

```text
temporal facts
entity relationships
fact invalidation/evolution
historical truth
graph-aware recall
```

Graphiti-derived graph state remains rebuildable/optional relative to Never4gA canonical sources.

---

# 12. Zep Adapter

Zep may implement:

```text
MemoryAugmentor
+
TemporalGraphProvider
```

Potential uses:

```text
prompt-ready context block
graph search
evolving user/workspace facts
observations/summaries
```

Never4gA should preserve raw source provenance rather than treating an assembled provider context string as canonical truth.

---

# 13. Retrieval Integration

External memory is another candidate lane:

```text
Native lexical
Native vector
Native graph
Recent episodes
External memory
Temporal graph
      ↓
normalized candidates
      ↓
fusion / policy
      ↓
Context Pack
```

External memory does not bypass:

- authority policy;
- staleness/currentness checks;
- workspace scope;
- context budget.

---

# 14. Provider Failure

If Mem0/Zep/Graphiti is unavailable:

```text
Never4gA continues
```

Startup/retrieval falls back to native memory.

A memory provider outage must not prevent ordinary work.

---

# 15. Provider Storage Identity

Never4gA may store mappings:

```text
Never4gA concept UUID
↔ provider memory/entity/episode ID
```

Provider IDs never replace Never4gA UUIDs.

---

# 16. Rebuild / Resync

Providers should declare whether projections are:

```text
rebuildable
partially rebuildable
provider-exclusive
```

Never4gA strongly prefers rebuildable integrations.

Future commands may include:

```text
never4ga memory provider status
never4ga memory provider sync
never4ga memory provider rebuild
never4ga memory provider clear-derived
```

---

# 17. Capabilities

Example capability set:

```text
search
prompt_context
episodic_ingest
semantic_ingest
entity_memory
temporal_facts
history
graph_search
delete
export
self_hosted
```

Never4gA checks capabilities rather than provider names.

---

# 18. Security / Privacy

Before enabling a provider, expose:

```text
hosting model
data leaving machine?
retention
authentication
workspace scope
what gets ingested
deletion/export capability
```

Self-hosted and cloud providers are both valid when explicitly chosen.

---

# 19. Testing

All MemoryAugmentor implementations should pass a common contract suite.

Likewise TemporalGraphProvider implementations.

Tests include:

- provider unavailable;
- duplicate ingestion;
- workspace scoping;
- provenance survives normalization;
- provider IDs do not leak as canonical IDs;
- external result can be omitted without breaking native context;
- promoted concept remains valid if provider is later removed.

---

# 20. Implementation Timing

Do not implement external memory providers in the MVP.

But define the ports early enough that:

```text
ContextAssembler
MemoryService
SessionCloseout
```

do not hard-code native-only assumptions.

Initial providers:

```text
NativeMemoryProvider
NullExternalMemoryProvider
NullTemporalGraphProvider
```

Actual external adapters follow retrieval/memory evals.
