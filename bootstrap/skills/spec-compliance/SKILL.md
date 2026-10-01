---
name: spec-compliance
description: Ensures Never4gA implementation work follows the repository's normative specifications and records explicit ADRs instead of silently drifting. Use for any Never4gA implementation task.
---

# Spec Compliance

Before implementing:

1. identify current milestone;
2. identify normative specs governing the change;
3. list the exact acceptance criteria;
4. identify any conflict between requested implementation and spec.

During implementation:

- do not change architecture by accident;
- do not hard-code future backend assumptions;
- preserve unknown schema data;
- keep canonical vs derived state boundaries explicit.

If a spec appears wrong:

1. stop;
2. describe the concrete issue;
3. draft an ADR;
4. update spec only after the decision is explicit;
5. then implement.

At completion:

- state which spec sections were satisfied;
- note any intentionally deferred work;
- report any new architectural risk discovered.
