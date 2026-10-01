---
type: documentation
id: 01a03502-3e5e-7361-81cf-6f82a1ac8d7a
schema: never4ga/0.1
title: "Never4gA Context Acquisition & Mechanical Tools Contract v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/07_CONTEXT_ACQUISITION_AND_MECHANICAL_TOOLS.md"
---
# Never4gA Context Acquisition & Mechanical Tools Contract v0.1

**Status:** Normative architectural constraint  
**Purpose:** Minimize context cost, improve determinism, and prevent unnecessary LLM use.

---

# 1. Principle — Mechanical First

Never4gA MUST prefer deterministic, inspectable, non-LLM mechanisms for context discovery whenever they can answer the question sufficiently.

The intended order is:

```text
mechanical signals
      ↓
deterministic filtering
      ↓
lexical / graph / structured retrieval
      ↓
bounded candidate set
      ↓
optional semantic/LLM enrichment
      ↓
Context Pack
```

The LLM should reason over a **small selected evidence set**, not discover the entire workspace by reading everything.

---

# 2. Why

Mechanical context acquisition provides:

- lower token use;
- lower latency;
- repeatability;
- explainability;
- operation while no model is available;
- fewer hallucinated relationships;
- easier testing;
- easier caching;
- predictable startup behavior.

Never4gA does not use an LLM merely because one is present.

---

# 3. Context Acquisition Stages

## Stage A — Scope Resolution

Use mechanical signals first:

```text
current working directory
Git repository root
repo-to-workspace registry
optional repo marker
workspace parent chain
explicit workspace ID
configured external-project mapping
```

Output:

```text
resolved workspace
parent workspace chain
repository identity
configured PM project
```

No LLM required.

---

## Stage B — Structural Metadata

Read/index:

```text
frontmatter type
workspace UUID
parent
profiles
status
lifecycle
authority
domains
tags
stale_after
verified
relations
sources
```

Use schema rules to include/exclude candidates.

No LLM required.

### `90_Archive/` is excluded, at every depth

No document under `90_Archive/` enters a Context Pack, through any stage or
lane, however relevant it is. Detachment is a property of the material rather
than of the interface reading it, so no depth relaxes this and `deep` is not an
exception.

`core/01` has always called the archive "intentionally detached historical
material", and until ADR-0031 that was the only sentence on the subject —
detachment was specified for filing and left open for retrieval. The two
readings were not equivalent. Measured 2026-09-03 on a 1,429-concept vault:
asked *what the frontmatter convention is for notes*, `deep` returned six
archived documents in the first ten, ranked an archived note about the
*superseded* convention first, and did not return `core/02` — the schema in
force — at all.

The mechanism that admitted them is the one to watch for when adding a lane: a
type-based query with no path filter. `30_Knowledge/` belongs to no workspace,
so a workspace filter excludes it and a broad depth has to admit knowledge some
other way; admitting it *by type* also admits every archived note, because an
archived note keeps its type. Exclusion is by location, and location is the
only thing that distinguishes them.

The accepted cost is that genuinely useful history becomes unreachable through
context acquisition. The remedy is deliberate rather than technical: material
still worth retrieving does not belong in `90_Archive/`.

No LLM required.

---

## Stage C — Filesystem / Document Structure

Mechanically extract:

```text
paths
headings
heading hierarchy
links
line ranges
content hashes
document/chunk identities
index.md navigation
workspace semantic folders
```

Use structural location as a ranking/scoping signal.

No LLM required.

---

## Stage D — Lexical Retrieval

Use:

```text
SQLite FTS5
BM25
exact identifiers
aliases
titles
headings
tags
domains
```

This handles identifiers and explicit terminology efficiently.

No LLM required.

---

## Stage E — Relationship Retrieval

Use:

```text
typed relations
Markdown links
workspace hierarchy
decision supersession
goal → plan relationships
requirement → work references
entity links
```

Traverse within bounded depth.

No LLM required.

---

## Stage F — External Operational State

Use APIs/tools mechanically.

Examples:

### Git

Potential signals:

```text
repo root
branch
working tree status
changed files
recent commits
commit hash
diff/stat
```

Git is an external working-state source, not canonical Never4gA memory.

### OpenProject

Potential signals:

```text
current ticket
status
assignee
priority
milestone
updated_at
relationships
```

Use provider API responses rather than asking an LLM to infer ticket state.

### Future sources

```text
CI
GitHub/Jira/Linear
calendar
issue trackers
build/test output
```

Each uses a typed source adapter.

---

# 4. Mechanical Context Provider Interface

Phase F adds a conceptual port:

```text
ContextSignalProvider
```

A provider contributes structured signals/candidates without owning Context Pack assembly.

Examples:

```text
WorkspaceSignalProvider
GitSignalProvider
WorkManagementSignalProvider
FreshnessSignalProvider
RecentActivitySignalProvider
```

Interface semantics should remain simple:

```text
supports(request)
collect(request, resolved_scope)
→ structured signals
```

Providers are optional and capability-driven.

---

# 5. Retrieval Is Not the Same as Context Acquisition

Context acquisition includes more than search.

Example:

```text
cwd
→ workspace

Git branch
→ current working state

OpenProject #431
→ operational task

frontmatter
→ authority/lifecycle

FTS
→ relevant docs

relations
→ dependent decisions
```

Retrieval is one component inside the larger acquisition pipeline.

---

# 6. Deterministic Candidate Reduction

Before any expensive semantic or LLM stage, Never4gA SHOULD reduce the candidate universe using:

```text
workspace scope
type constraints
authority
status/lifecycle
freshness
exact identifiers
metadata
lexical rank
graph distance
time/recent activity
```

Example:

```text
50,000 concepts
    ↓ workspace + parent/global scope
2,000
    ↓ relevant types
400
    ↓ FTS/BM25
30
    ↓ graph/authority/freshness
12
    ↓ optional semantic rerank
6
    ↓ Context Pack
```

Exact numbers are illustrative.

---

# 7. Optional LLM Context Enrichment

LLMs MAY be used after deterministic reduction for tasks mechanical tools cannot reliably solve.

Examples:

```text
query rewriting
semantic query expansion
ambiguous intent classification
candidate reranking
summarization
semantic contradiction suggestion
memory extraction proposal
```

These operate behind explicit interfaces such as:

```text
QueryEnricher
CandidateReranker
ContextSummarizer
ContradictionAnalyzer
MemoryExtractor
```

They are optional.

The system MUST have useful behavior when all LLM enrichment providers are disabled.

---

# 8. LLM Use Must Not Be Hidden

When an LLM enrichment stage is used, debug/explain output SHOULD identify:

```text
stage
provider/model if known
input candidate count
output candidate count
reason
```

Never4gA should distinguish:

```text
mechanically selected
```

from:

```text
model-derived inference
```

---

# 9. Mechanical Summaries

Never4gA SHOULD generate non-LLM summaries where possible.

Examples:

```text
workspace title + description
active goal titles
accepted decision titles
counts by lifecycle
recent activity titles
PM ticket title/status
Git branch/status
```

A startup context can often be highly useful without generating a prose summary.

---

# 10. Token / Size Budgeting

Budget enforcement is mechanical.

The Context Assembler tracks:

```text
characters
estimated tokens when tokenizer available
item count
full-document bytes
```

It applies priority ordering and truncation/reference substitution.

**Required reading is never budgeted away** (ADR-0043). For the workspace a
startup resolves to, it is the workspace page, the workspace pages of its
parent chain, every live `type: context` document in its `Context/`, its
newest activity log that is not a roll-up, and its required standards
(ADR-0050). A standard is required when it is live, applies to the workspace
(`core/02` §21.15), and either sits in `50_System/Standards/` or says
`required_reading: true`; one that says `required_reading: false` is not. A
standard that `excepts` a required standard is required too. Required bodies
go inline, in priority order, while the response stays under a transport
limit of 24,000 characters. A required document that does not fit is listed
with its absolute path and size, to be read in full with the agent's own file
reader. It is never truncated or dropped. The budget governs everything else.

Every other standard that applies, and every accepted decision, is binding: it
is always listed, and never dropped by a category cap or the item ceiling. Its
body is placed after required reading and before anything optional. A
standard listed without its body carries its `description` in the index, so
the reader knows what it governs and when to read it. Every startup pack
reports the required-reading total against a ceiling of 200,000 characters, and
`doctor` reports a workspace over it. Neither of them cuts anything.

A reference substitution states which of these caused it (ADR-0042): the body
alone is larger than the pack's character budget (`larger_than_budget`), the
higher-priority bodies used the room first (`budget_spent`), or the pack's
limit on full documents was reached (`document_limit`).

Do not ask an LLM to decide whether a 30,000-token startup pack is too large.

---

# 11. Context Cache

Mechanically assembled Context Packs MAY be cached by:

```text
workspace
task fingerprint
source hashes
provider update timestamps
schema/config versions
```

Caches are invalidated when relevant source state changes.

Context cache is derived.

---

# 12. Context Provenance

Every Context Pack item SHOULD identify how it arrived.

Examples:

```text
workspace_required
exact_identifier_match
fts_rank_2
relation_depends_on_depth_1
recent_activity
openproject_current_item
git_changed_file
vector_rank_4
llm_rerank_2
```

This enables debugging and evals.

---

# 13. Git Context Adapter — Planned

A future Git mechanical adapter SHOULD support read-only context acquisition first.

Possible operations:

```text
resolve repository
current branch
HEAD commit
status
changed paths
recent commit summaries
diff stats
targeted diff retrieval
```

Never4gA should prefer invoking the installed `git` CLI over parsing `.git/` internals itself unless a compelling reason appears.

Git context remains external operational evidence.

---

# 14. Build/Test Mechanical Context

Future agent context may mechanically inspect:

```text
test command result
lint result
build result
CI state
coverage output
```

Again:

```text
tool result
```

is preferable to:

```text
LLM guesses whether tests pass.
```

---

# 15. Context Startup Target

Startup should normally require **zero Never4gA-owned model calls**.

Ideal startup:

```text
resolve cwd
read workspace projection
read required standards metadata
read recent activity projection
read PM summary cache/API
return structured bounded pack
```

Then the coding agent itself decides what to inspect next.

---

# 16. Acceptance Tests

From early implementation:

### Mechanical startup

Given a mapped repo, startup resolves the correct workspace without invoking any LLM provider.

### Exact ticket

Given `OP #431`, the corresponding external work item is retrieved mechanically.

### Exact decision

Given a decision title/ID, FTS/metadata retrieves it without semantic search.

### Budget

Startup never exceeds configured output budget without replacing low-priority bodies with references.

### Explainability

Each included context item reports acquisition reason.

### No-LLM mode

With:

```text
VectorIndex disabled
all LLM enrichers disabled
all external memory providers disabled
```

Never4gA still provides useful workspace startup and focused lexical/graph retrieval.

---

# 17. Architectural Rule

Never4gA's intelligence is not defined by how many LLM calls it makes.

A successful implementation pushes:

```text
identity
scope
filtering
lookup
validation
ranking where deterministic
state inspection
maintenance
```

into mechanical systems and reserves model reasoning for ambiguity, synthesis, semantic inference, and judgment.
