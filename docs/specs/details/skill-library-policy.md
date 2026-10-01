---
type: documentation
id: 01a03502-3e5f-766c-922f-90f6b5dfae26
schema: never4ga/0.1
title: "Never4gA Skill Library Policy v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/SKILL_LIBRARY_POLICY.md"
---
# Never4gA Skill Library Policy v0.1

## 1. Canonical location

```text
50_System/Skills/<skill-name>/
```

Every canonical portable Skill follows the Agent Skills open format.

Minimum:

```text
skill-name/
└── SKILL.md
```

Preferred:

```text
skill-name/
├── SKILL.md
├── references/
├── scripts/
├── assets/
├── LICENSE.txt
└── ORIGIN.md
```

Only include files that serve a purpose.

---

## 2. Skill design rules

- One Skill, one coherent job.
- Descriptions must state what it does and when to use it.
- Prefer procedural instructions over broad background knowledge.
- Keep the main `SKILL.md` compact.
- Move detailed reference material into `references/`.
- Prefer instructions over scripts.
- Prefer deterministic self-contained scripts when a script is justified.
- Do not bundle secrets.
- Do not bundle user-specific project context into global Skills.
- Do not duplicate Standards inside Skills.

---

## 3. Never4gA metadata

Recommended Agent Skills metadata:

```yaml
metadata:
  never4ga-source: authored
  never4ga-version: "0.1.0"
  never4ga-review-status: reviewed
  never4ga-exposure: global
```

Allowed management values are maintained by Never4gA, not the Agent Skills standard.

### Source

```text
authored
vendored
forked
```

### Review status

Suggested:

```text
unreviewed
reviewing
reviewed
blocked
```

### Exposure

```text
bootstrap
global
workspace
library
```

---

## 4. External skill adoption checklist

Before adoption:

- [ ] Record upstream source.
- [ ] Record upstream version/commit if available.
- [ ] Review license.
- [ ] Review `SKILL.md`.
- [ ] Review scripts line by line.
- [ ] Review referenced files relevant to behavior.
- [ ] Identify network use.
- [ ] Identify file writes.
- [ ] Identify shell commands.
- [ ] Identify external package/tool dependencies.
- [ ] Remove unnecessary dependencies where practical.
- [ ] Add/retain attribution.
- [ ] Classify as vendored or forked.
- [ ] Test trigger behavior.
- [ ] Test expected output/workflow.
- [ ] Choose exposure scope.
- [ ] Human approve before broad deployment.

---

## 5. Updates

External Skill updates are never automatic.

Process:

```text
upstream change
   ↓
review diff
   ↓
review scripts/dependencies
   ↓
run tests
   ↓
human approve
   ↓
update canonical vendored/forked source
   ↓
adapter sync
```

---

## 6. Scripts

A script may be included when:

- deterministic transformation is valuable;
- repeated shell/code behavior would otherwise be error-prone;
- it reduces prompt/context complexity.

A script should not exist merely because automation is possible.

Dependencies must be explicit.

Never4gA should eventually be able to report:

```text
Skill: xyz
Requires:
- git
- jq
- network access
Writes:
- workspace files
```

before enabling it.

---

## 7. Skill tests

Every important Skill SHOULD have scenario/eval cases.

At minimum test:

- obvious trigger;
- non-trigger;
- ambiguous trigger;
- expected procedure;
- prohibited behavior;
- failure path;
- dependency unavailable;
- cross-client portability where claimed.

---

## 8. Initial bootstrap set

The eight fixed by ADR-0018. `core/04` sections 22 and 32 carry the same list;
this was a third copy of it, and was the last one still naming the pre-ADR-0018
spellings.

```text
never4ga-startup
never4ga-context
never4ga-capture
never4ga-create
never4ga-doctor
never4ga-decide
never4ga-checkpoint
never4ga-wrap
```

Do not build a giant Skill catalog before these work reliably.
