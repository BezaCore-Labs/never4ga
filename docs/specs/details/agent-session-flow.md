---
type: documentation
id: 01a03502-3e5e-7361-81cf-6f867b2232e8
schema: never4ga/0.1
title: "Never4gA Agent Session Flow v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/AGENT_SESSION_FLOW.md"
---
# Never4gA Agent Session Flow v0.1

## Normal user experience

The user should be able to do this:

```bash
cd ~/Projects/sparrow
claude
```

Then ask:

```text
Work on the member search ticket.
```

Never4gA should make the session behave approximately as follows.

---

## 1. Native startup

Claude/Codex/Antigravity loads its normal global and repo-specific instructions.

The tiny Never4gA bootstrap is included.

---

## 2. Startup Skill

The agent invokes:

```text
never4ga-startup
```

The Skill contacts the local Never4gA core.

---

## 3. Workspace resolution

Input:

```text
cwd=/home/.../Projects/sparrow
client=claude-code
```

Resolution:

```text
repo mapping
   ↓
Sparrow workspace UUID
   ↓
parent: BezaCore Labs
```

---

## 4. Compact startup pack

Returns:

```text
Workspace identity
Current lifecycle/state
Parent context pointers
Repo guidance files
Required standards
Recommended Skills
PM provider mapping
Important stale/conflict warnings
Small current-work summary
```

Not the whole vault.

---

## 5. Task focus

User mentions “member search ticket.”

The agent requests focused context.

Never4gA resolves:

```text
OpenProject work item
        +
linked requirements
        +
architecture
        +
decisions
        +
recent activity
        +
standards
```

---

## 6. Work

Agent edits the normal external source repository.

Never4gA does not proxy model calls.

---

## 7. Checkpoint

After a meaningful milestone, the agent records:

```text
what changed
current state
unresolved issue
next step
relevant ticket/commit
```

This becomes episodic continuity.

---

## 8. Closeout

At task/session completion:

```text
activity summary
candidate durable knowledge
candidate decision updates
workspace-state updates
candidate PM update
```

Policies determine what can be written automatically.

---

## 9. Next tool

Tomorrow:

```bash
cd ~/Projects/sparrow
codex
```

Codex performs the same Never4gA startup.

It does not need Claude's transcript.

It retrieves the durable context/checkpoint through Never4gA.

That is the intended cross-agent continuity model.
