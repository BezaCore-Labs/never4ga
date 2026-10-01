---
type: documentation
id: 01a03502-3e5f-766c-922f-90f47a71f9b7
schema: never4ga/0.1
title: "Never4gA Retrieval, Context & Memory Architecture v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/RETRIEVAL_CONTEXT_MEMORY.md"
---
# Never4gA Retrieval, Context & Memory Architecture v0.1

---

# 1. Three Different Problems

Never4gA keeps separate:

```text
RETRIEVAL
What existing material is relevant?

CONTEXT ASSEMBLY
What subset should this client receive now?

MEMORY
What new information from activity should persist?
```

Do not collapse them into one vector database.

---

# 2. Retrieval Inputs

A retrieval request may include:

```text
query
workspace
cwd
document types
domains
tags
lifecycle/status
authority
time constraints
relationship constraints
external work item
budget
```

Workspace scope should be resolved before broad retrieval when possible.

---

# 3. Retrieval Pipeline v0.1

```text
request
  ↓
resolve scope
  ↓
apply hard metadata filters
  ↓
lexical retrieval (FTS5/BM25)
  ↓
optional semantic retrieval
  ↓
graph/link expansion
  ↓
candidate deduplication
  ↓
rank fusion
  ↓
authority/freshness adjustments
  ↓
return evidence + reasons
```

---

# 4. Lexical Retrieval Is First-Class

Exact identifiers and jargon matter.

Examples:

```text
E36
OP-431
JWT
ASC-US
specific class/function names
```

FTS/BM25 remains useful even after vector search is added.

Never4gA will not replace lexical retrieval with embeddings.

---

# 5. Semantic Retrieval

Optional vectors improve conceptually similar retrieval.

Semantic retrieval is an additional candidate generator.

It is not trusted to determine:

- authority;
- lifecycle;
- supersession;
- workspace ownership;
- provenance.

Those come from structured metadata and relationships.

---

# 6. Hybrid Fusion

When multiple retrievers are enabled, v0.1 SHOULD use rank-based fusion such as Reciprocal Rank Fusion rather than trying to directly compare incompatible BM25 and vector scores.

Final ranking may apply bounded boosts/penalties for:

- exact workspace match;
- required parent context;
- authoritative content;
- human verification;
- recency where relevant;
- stale/deprecated content;
- supersession.

Ranking signals should be explainable in debug output.

---

# 7. Stale and Deprecated Content

Do not silently hide it.

A stale result may still explain:

- historical reasoning;
- prior state;
- why something changed.

Default behavior:

```text
current authoritative result
   ranked higher

stale/superseded result
   available with warning
```

---

# 8. Retrieval Result Contract

Every result should carry:

```text
concept ID
path
title
type
workspace
heading/section
excerpt
source lines
authority
verification tier
status/lifecycle
staleness
retrieval reasons
retriever ranks
```

This enables auditable context.

---

# 9. Context Pack

Never4gA's central output is a structured Context Pack.

A Context Pack is not one giant prompt.

Conceptual:

```yaml
workspace: ...
task: ...
parents: ...
current_state: ...
standards: [...]
skills: [...]
requirements: [...]
decisions: [...]
knowledge: [...]
recent_activity: [...]
work_items: [...]
warnings: [...]
references: [...]
```

Each included item records:

```text
why it was included
source concept
authority/currentness
full content or excerpt/reference
```

---

# 10. Context Levels

## Startup

Small orientation.

Target:

- workspace identity;
- parent chain;
- current state;
- critical constraints;
- mandatory standards;
- PM/repo mapping;
- recent meaningful activity;
- warnings;
- recommended Skills.

## Focused

Task-specific.

May add:

- requirements;
- decisions;
- architecture;
- research;
- Knowledge;
- relevant PM tickets;
- related activity.

## Deep

Explicit investigation.

Broader graph/history/global Knowledge.

Never default to deep.

---

# 11. Budgeting

Adapters may supply a target token budget if known.

Core Context Packs should remain structured independently of one provider tokenizer.

When token accounting is unavailable, use a conservative character/size budget.

Priority order:

```text
required rules/constraints
current workspace state
task-defining requirements/work item
accepted decisions
critical architecture/context
recent relevant activity
supporting knowledge
historical material
```

---

# 12. Progressive Disclosure

Prefer:

```text
title
summary/excerpt
reason
reference
```

then allow:

```text
concept.get(id)
```

rather than embedding whole long files.

This matches the progressive-disclosure philosophy of both OKF navigation and Agent Skills.

---

# 13. Memory Model

Never4gA begins with four memory classes.

```text
Working
Episodic
Semantic
Procedural
```

---

# 14. Working Memory

Purpose:

Current session/task state.

Storage:

Derived runtime state.

Examples:

- session ID;
- current workspace;
- current task;
- selected work item;
- focused concept IDs;
- temporary unresolved questions.

Working memory may expire.

It is not canonical durable knowledge.

---

# 15. Episodic Memory

Purpose:

What happened.

Canonical storage:

```text
<Workspace>/Logs/
```

One substantive Never4gA session SHOULD normally map to one `activity_log` concept.

Example:

```text
2026-08-22 - Claude Code - Member Search.md
```

The file may be updated by checkpoints during the session and finalized at closeout.

It captures outcomes, not full transcripts.

---

# 16. Episodic Log Structure

Recommended body:

```markdown
# Session

## Objective

## Starting Context

## Progress

## Changes

## Decisions / Candidates

## Blockers

## External Work

## Next Step
```

Frontmatter identifies:

- workspace;
- session ID;
- occurred time;
- generating actor;
- external provider session reference when available.

---

# 17. Semantic Memory

Purpose:

What is durably known.

Storage:

Canonical concept Markdown.

Examples:

- Knowledge notes;
- Workspace Context;
- Requirements;
- accepted Decisions;
- Entity records;
- Goals where applicable.

Semantic memory is not a separate vector-only memory database.

Vectors index it; they do not own it.

---

# 18. Procedural Memory

Purpose:

How work is done.

Canonical storage:

```text
50_System/Skills/
50_System/Standards/
```

Skills define procedure.

Standards define governing rules/expectations.

---

# 19. Memory Promotion

Default:

```text
agent observation
       ↓
working/episodic record
       ↓
candidate durable update
       ↓
validate + authorize
       ↓
semantic/procedural canonical write
```

Never4gA does not auto-promote every conversation statement.

---

# 20. Closeout

Agent closeout should produce:

```text
episodic update
candidate decisions
candidate knowledge
candidate context updates
candidate PM updates
unresolved questions
next-step recommendation
```

The runtime may automatically write the episodic log under permitted Class-1 policy.

Higher-authority writes follow stronger policy.

---

# 21. Cross-Agent Continuity

Continuity is based on durable state:

```text
Claude session
   ↓
activity log + code + PM state + durable docs
   ↓
Codex startup tomorrow
   ↓
retrieval/context pack
```

No provider transcript transfer is required.

---

# 22. Context Evaluation

Never4gA must eventually maintain retrieval/context eval cases.

Example fixture:

```text
Given:
  workspace = Sparrow
  task = implement member search
Expect:
  includes requirement X
  includes decision Y
  includes architecture Z
  excludes unrelated wedding notes
  does not prioritize superseded decision W
```

This becomes test-driven retrieval development.
