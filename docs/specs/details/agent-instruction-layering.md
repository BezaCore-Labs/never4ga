---
type: documentation
id: 01a03502-3e5e-7361-81cf-6f8594680069
schema: never4ga/0.1
title: "Never4gA Agent Instruction Layering v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/AGENT_INSTRUCTION_LAYERING.md"
---
# Never4gA Agent Instruction Layering v0.1

**Status:** Normative — governed by ADR-0011 (accepted 2026-08-23) and
ADR-0025 (accepted 2026-08-30), which replaced §2 and reshaped §§5, 6, 8, 9
and 10.
**Purpose:** Decide where each kind of agent instruction lives, how it reaches a
session, and which one wins when two disagree.
**Depends on:** `core/04` §§3–14, `core/02` §12, `core/03` §32,
[`details/SECURITY_CONFIGURATION.md`](security-configuration.md) §7

---

# 1. The Problem This Solves

`core/04` §3 defines five instruction surfaces and forbids collapsing them into
one file. It does not say which surface wins when two of them disagree.

That gap is not academic. A tool writes its own instruction file — `claude /init`
produces a `CLAUDE.md` — and now a repository holds two sets of instructions that
nobody reconciled. The session loads both. Whichever the model weights more
heavily decides the behaviour, and no part of the system noticed.

The observable failure is contradiction, not tampering. This document is about
making instructions arrive from the right place, in a known order, with
disagreements reported.

---

# 2. One Question, Not Three

**Rewritten 2026-08-30 by ADR-0025.** This section previously divided agent
instructions into three kinds — public-about-the-repository,
personal-about-the-repository, and about-the-user — and gave each a different
home. The classification was sound and its failure mode was a public leak: it
had to be applied per instruction, by a writer, at the moment of writing, and
the first time it was applied at scale it was got wrong in four public
repositories at once. ADR-0025 dissolves it.

There is now one question, and it is asked about a **file**, not a line:

> Is this file addressed to an agent, or is it documentation the repository
> publishes about itself?

| | Home | Committed? |
|---|---|---|
| **An agent instruction file** — `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and every client-specific equivalent | generated pointer on disk, content in the vault | **no** — gitignored |
| **Repository documentation** — test commands, code layout, layering rules, build steps, review policy | `README.md`, `CONTRIBUTING.md`, `docs/` | yes |
| **A rule about how the user works** — path naming, remote topology, attribution, decision process | vault, `50_System/Standards/` or a workspace Standard | no |

`core/04` §10's content is untouched by this. What moved is not the material but
the **filename**: an agent-addressed name in a repository now holds a generated
pointer and nothing else, so there is no file in which a writer can misclassify
a line. A repository still describes itself, still does so in tracked files, and
a stranger who clones it without Never4gA can still run its tests.

**The pointer occupies the name on purpose.** A file at `CLAUDE.md` is what
prevents `claude /init` writing one there. See §5.

**One class of repository is excepted** — one whose agent contract is part of
what it ships. See §8.

---

# 3. Delivery

Two paths, arriving independently. Neither is a copy of the other.

```text
cwd = ~/Projects/never4ga
        │
        ├─► workspace resolution (core/04 §12)
        │        └─► applicable Standards ─► Context Pack
        │                                    (the personal half)
        │
        └─► generated pointer at ./AGENTS.md, ./CLAUDE.md, …
                 └─► names the workspace, the startup command, and the
                     repository's own tracked documentation (core/04 §11)
```

Two entry points, and ADR-0025 changed which one is ordinary.

The **global managed block** is one small block per tool per machine, written by
`never4ga adapters sync` into the client's own global instruction file
(`core/04` §7, §8). It carries two things: invoke `never4ga-startup` when
available, and treat a repository's `AGENTS.md` as authoritative. It covers
every repository the user will ever open, including ones Never4gA has never
seen, and it remains the floor.

The **repo-side pointer** is now written into every *mapped* repository rather
than offered as an exception, under ADR-0025 part 2. It is generated, gitignored
and carries no content of its own. Since ADR-0025 it does three jobs rather than
one: it makes arrival mechanical (§3.2), it names the repository's own tracked
documentation so §11's guidance report survives a session that never calls
startup, and it occupies the filename so a client cannot generate into it (§5).

**An unmapped repository still requires no Never4gA-written file**, which is what
keeps §10 and §12 satisfied and what `core/04` §12's "the user is never required
to put Never4gA metadata in every Git repository" means after ADR-0025.

## 3.1 The global path is advisory

Measured, not assumed — the workspace's `Research/q-009-spike-result.md`.

A managed block loads verbatim and a session will quote it back. On a substantive
task the client reads the repository's `AGENTS.md` and follows it. On a trivial
turn it may see the file, know the rule, and decline to read it.

That is defensible: `core/04` §5 says "at the beginning of a **substantive**
session", and reading a repository's instructions to answer a passing question is
waste. But it must be stated rather than assumed:

> The global bootstrap delivers instructions **when work happens**, not on every
> turn. Compliance is a property of the model and may change between releases.

## 3.2 The mechanical path requires a file in the repository

A client that supports include directives can be pointed at `AGENTS.md` by a
one-line file inside the repository, expanded when instructions load rather than
obeyed at the model's discretion. The same directive placed in a *global*
instruction file does not work: it resolves against the client's own
configuration directory, not the working directory.

So the two paths are complementary:

| | File in repo | Guarantee | Setup cost |
|---|---|---|---|
| Global managed block | none | advisory | one write per tool per machine |
| Repo pointer to `AGENTS.md` | one line | mechanical | one file per repository |

The global path was measured across four clients: two honour it on every turn,
two skip it on trivial turns, and the split does not follow vendor lines. It is
therefore reliable for substantive work and unreliable in general.

The block is the floor and covers every repository the user will ever open. **The
pointer is the norm in a mapped repository** since ADR-0025, rather than
something offered where instructions must never be skipped.

A client that reads `AGENTS.md` **natively** still needs no block — Codex does —
but it does not follow that it needs no pointer. Before ADR-0025 the pointer's
only job was mechanical arrival, so a native reader made it redundant. It now
also carries §11's guidance report and occupies the filename, and neither of
those is done by native reading. Adapter sync should still prefer the highest
rung a client supports for *delivery*; the pointer is written regardless.

---

# 4. Precedence

Rank by `core/02` §12 `authority` first, then by specificity. `derived` never
outranks `authoritative`.

| Source | Authority | Outcome |
|---|---|---|
| `AGENTS.md`, human-authored | authoritative, most specific | wins within its domain |
| Workspace Standard | authoritative | wins where the repository is silent |
| Parent-workspace / global Standard | authoritative, least specific | wins where both are silent |
| Agent-generated tool file | `derived` | never outranks either; reported |

Ranking by locality alone fails, because the most local file in a repository is
the one a tool generated without anybody authoring it.

The convention that makes this work without any declaration inside a repository:

> **`AGENTS.md` is a repository's authoritative agent instruction file.** A
> tool-specific instruction file in a repository that also has `AGENTS.md` is
> subordinate, and the discrepancy is reported rather than silently followed.

Most instructions never collide. §10's domain split means a repository's test
command and a user's naming Standard are about different things and compose.
Precedence decides genuine collisions only.

---

# 5. Tool-Generated Instruction Files

**Reshaped 2026-08-30 by ADR-0025 part 3.** This section used to describe how to
*handle* a file a tool wrote. Most of the time there is now no such file,
because the name is taken.

## 5.1 Prevention, where prevention actually works

ADR-0011 part 1 ranks prevention lowest of the three integrity layers and calls
it demonstrably bypassable. That judgement is about content — a permission rule
or a hook trying to stop a capable agent editing a file it can reach.

Occupying a filename is a different mechanism and it holds, because it is the
filesystem declining to put a second file at one path rather than a model
choosing to comply. A generated pointer at `CLAUDE.md` means `claude /init` has
nowhere to write. This is the one place in the design where prevention is the
primary control rather than defence in depth.

It is not absolute, and the limit should be stated rather than found: a client
that *overwrites* an existing instruction file rather than declining to create
one defeats it. That case falls back to detection — §7's byte-equality check
reports the pointer as modified.

## 5.2 When one exists anyway

A file a tool wrote is a discovered extension of class `rule` (`core/09` §3), and
follows the ordinary lifecycle: **discover → register → optionally adopt**
(`core/09` §§12–13). It is never adopted implicitly.

This still happens in three cases: an unmapped repository, a repository last
synced before a client existed, and a client that overwrites. Default behaviour
is **report, do not write**:

1. startup and `adapters doctor` notice an instruction file Never4gA does not
   manage;
2. they report it as unmanaged and subordinate;
3. nothing is modified.

If the user adopts it, `adapters sync` replaces it with a managed pointer under
every constraint in §6, recording origin, review date and hash per `core/09` §13.
An unmanaged file is never overwritten (`core/04` §25.1, §26.1).

## 5.3 A tracked agent file is itself a finding

After ADR-0025 part 1, an agent instruction file that is **committed** in a
repository not declared under §8 is a reportable condition, not merely an
unmanaged one. It is the state the ADR exists to prevent, and it is the state
every repository except `never4ga` was in on 2026-08-30.

---

# 6. Writing Into a Repository

Permitted only under ADR-0011 part 2. **ADR-0025 made this the normal path
rather than an exception**, and that raises rather than lowers the importance of
the list below. The six conditions were written for a rare, opt-in write; they
now govern a write that happens in every mapped repository. Restating them,
because this is the part most likely to be loosened by accident:

- the repository is in the local repo mapping registry;
- the user invoked the command — never on discovery, never on a timer;
- a diff is shown first;
- ownership and content hash are recorded;
- an existing file receives a delimited managed block, never a wholesale
  overwrite;
- an unmapped or third-party repository is never written to.

Dry-run is required ([`details/API_CLI_MCP_CONTRACT.md`](api-cli-mcp-contract.md) §10), and the operation is
idempotent (§11).

**Condition 2 is the one carrying the weight now.** *The user invoked the
command — never on discovery, never on a timer* is the whole of what stops a
routine write becoming an ambient one across every mapped repository. It must
not be relaxed because the write has become ordinary.

**`.gitignore` is a second file.** ADR-0025 part 1 requires an ignore rule
alongside the pointer, and `.gitignore` is a repository-owned file that
Never4gA did not author. It receives a delimited managed block under condition
5, exactly like any other existing file, and is never rewritten wholesale.

---

# 7. What `adapters doctor` Verifies

Extending `core/04` §45 and `core/09` §20 to instruction surfaces:

```text
global managed block present and matching its expected content
managed repo pointer matches its regenerated form, byte for byte
repository has AGENTS.md
repository has a tool-specific instruction file alongside AGENTS.md   → report
an instruction file appeared that Never4gA does not manage            → report
a managed file was edited locally                                     → report, never silently restore
a committed agent instruction file in a repository not excepted by §8 → report (ADR-0025 §5.3)
the .gitignore rule for agent files is absent or edited               → report
```

Byte-equality against regenerated content is what makes this cheap and certain: a
managed pointer is fully derivable from workspace, tool and adapter version, so
verification is a comparison rather than a heuristic. This is the detection layer
of ADR-0011 part 1, and it must not depend on the user reading a diff.

Verification proves a file is **unchanged**. It never proves two documents
**agree**. Contradiction between separately authored files is caught by
precedence and reporting, not by hashing.

---

# 8. The Excepted Class

**Replaced 2026-08-30 by ADR-0025 part 5.** This section previously described a
one-off event: the day `never4ga`'s own `AGENTS.md` would shed its personal half
into the vault, gated on four conditions of which Q-007 was the hard one. All
four have since held, and the split happened with the 2026-08-24 documentation
migration. What replaces it is the general rule that event turned out to be an
instance of.

> A repository whose **agent contract is part of what it ships** tracks its own
> `AGENTS.md`. Every other mapped repository gitignores a generated pointer.

The test is not "is this repository important" or "is it public". It is whether
somebody who clones the repository, and who is not the user, needs the agent
contract in order to work on it. `never4ga` is the case that exists: its
`AGENTS.md` is the implementation contract, states the layering rules and the
specification-change process, and must be present in a clone made by someone who
has never run Never4gA.

**Declared as data, per repository** — in the local repo mapping registry
(`core/04` §13) or the §14 marker — and never hardcoded to a name. A special
case in the registry is a row; a special case in the sync engine is a branch,
and a branch grows a second one.

An excepted repository's `AGENTS.md` is human-authored and tracked, so §2's
question still applies to every line in it: it may describe the repository, and
it may not carry a rule about how the user works. Those reach it through the
Context Pack like anywhere else.

The migration discipline stated here is still right and generalises: **move with
overlap, never as a single cut.** Put the material in the vault, leave the
repository copy in place, run a full session against the delivered version, then
delete.
---

# 9. Limits

Stated so they are not discovered later as surprises.

- **A clone gets no agent instruction file.** Added by ADR-0025. Outside the §8
  excepted class, the pointer is gitignored, so someone who clones the
  repository receives none of it. This is the accepted cost of the design, and
  what limits it is ADR-0025 part 4: the repository's own documentation stays
  tracked, so a clone can still be read and its tests still run. `core/04` §44's
  "repo-native instructions still apply" is narrower than it was — it now means
  `README.md`, `CONTRIBUTING.md` and `docs/`, not an agent file.
- **Nothing reaches a session that never invokes startup.** `core/04` §44 accepts
  this and requires graceful degradation: repository documentation still
  applies, and no ordinary work is blocked.
- **The global path depends on model compliance** (§3.1). The mechanical path
  exists for cases where that is not good enough.
- **Prevention is bypassable, with one exception.** Per-tool permission rules and
  hooks are defence in depth, never the guarantee. Occupying a filename is the
  exception (§5.1), and it fails against a client that overwrites rather than
  declines.
- **Hashing proves files unchanged, not consistent** (§7).
- **Cross-tool parity is measured for four clients, for one capability.**
  `core/04` §48's requirement holds for Claude Code, Codex, Antigravity CLI and
  Antigravity 2.0 on substantive work. Only global instruction delivery was
  measured; Skill deployment paths were not. **Antigravity IDE is a separate
  product and is out of scope for v0.1** (the maintainer, 2026-09-01, work item 944):
  it is not installed here, a client path is measured rather than reasoned out
  (`core/04` §9), and [`tool-adapter-matrix.md`](tool-adapter-matrix.md) now
  says so where it used to leave the surface merely unmeasured.
- **A GUI surface has no scriptable entry point.** Its acceptance test can only
  be a manual check or a filesystem assertion, never end-to-end behaviour.

---

# 10. Acceptance Tests

Extending `core/04` §48.

### Delivery

A session started in a mapped repository arrives holding the generated pointer,
resolves the workspace it names, and reads the repository documentation the
pointer lists. A session started in an *unmapped* repository, with no
Never4gA-written file in it at all, still arrives holding that repository's own
`AGENTS.md` if one exists — the global block's floor.

### Parity

The same repository resolves to the same workspace, and yields semantically
equivalent Standards, across every installed client. Formatting may differ.

### Precedence

A repository containing both an authored `AGENTS.md` and a generated
tool-specific file: the `AGENTS.md` rule governs, and the generated file is
reported as unmanaged and subordinate.

### Non-interference

`adapters sync` in an unmapped repository writes nothing. In a mapped repository
without explicit invocation it writes nothing. With an existing user-authored
file it preserves every unrelated line, `.gitignore` included.

**Amended 2026-09-30 by ADR-0049.** Explicit invocation means asking for
repository writes by name: `adapters sync --apply` deploys to agent clients
only, and writes nothing into any repository unless `--repositories` is also
given. An existing managed `.gitignore` block that differs from the current one
only in the comment lines between its markers is left as it is. It is
rewritten when the files it ignores change, or when it is malformed.

### Nothing published

After `adapters sync` in a mapped repository not excepted by §8, `git status`
is clean and `git ls-files` names no agent instruction file. This is the test
that would have caught the 2026-08-29 leak.

### Occupation

In a mapped repository holding a generated pointer, a client's own instruction
generator produces no new file. Where it overwrites instead, `adapters doctor`
reports the pointer as modified.

### Detection

An edit to a managed pointer is reported by `adapters doctor` without the user
inspecting a diff. An instruction file appearing where Never4gA manages none is
reported.

### Degradation

With Never4gA unavailable, a session in a mapped repository still works: the
pointer is on disk whether or not the service is running, it names the
repository documentation, and the session says shared context is unavailable.

In a *fresh clone*, where the pointer was never written, the session works from
`README.md`, `CONTRIBUTING.md` and `docs/` and says the same thing. That it can
still run the repository's tests is the acceptance criterion for ADR-0025
part 4.
