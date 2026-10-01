---
type: documentation
id: 01a03502-3e5f-766c-922f-90f5fdf714ee
schema: never4ga/0.1
title: "Never4gA Security, Configuration & Local-Service Policy v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/SECURITY_CONFIGURATION.md"
---
# Never4gA Security, Configuration & Local-Service Policy v0.1

---

# 1. Threat Model Scope

Never4gA is initially a single-user local application.

It still handles:

- personal knowledge;
- potentially sensitive project docs;
- PM credentials;
- agent write capabilities;
- filesystem access.

Local-first does not mean security-free.

---

# 2. Network Defaults

Default service:

```text
127.0.0.1:<configured-port>
```

Rules:

- no LAN bind by default;
- no public bind by default;
- CORS disabled by default;
- no inbound webhook listener by default;
- remote mode requires explicit configuration.

---

# 3. Local API Credential

Generate a random local service credential.

Store outside the vault.

CLI and MCP bridge read it locally.

Do not print it in normal logs.

Do not include it in agent context.

---

# 4. Secret Store

Preferred:

```text
OS credential/keyring service
```

Fallback:

```text
local secrets file
```

with restrictive filesystem permissions.

The fallback must produce a clear warning.

No secrets in:

```text
workspace.md
AGENTS.md
SKILL.md
Git-tracked config
```

---

# 5. Connection Registry

Canonical workspace:

```yaml
connection: work_openproject
```

Local configuration maps:

```text
work_openproject
  provider = openproject
  base_url = ...
  secret_ref = ...
```

Moving a vault does not expose credentials.

---

# 6. Agent Write Policy

Policies apply by write class.

Default recommended personal policy:

```text
Class 0 read
  allow

Class 1 provisional capture/checkpoint
  allow once user enables it

Class 2 canonical authoritative mutation
  explicit action / validation

Class 3 external side effect
  explicit user-requested action
```

Future users may configure stricter policies.

---

# 7. Filesystem Boundary

Canonical vault writes are restricted to the configured vault path.

External repository writes are performed by the coding agent itself, not Never4gA core, except for the narrow case below.

## 7.1 Instruction pointers in mapped repositories

ADR-0011 adds one exception. `never4ga adapters sync` MAY write Never4gA-owned instruction pointers and adapter configuration into a repository, subject to **all** of:

1. the repository is present in the local repo mapping registry (`core/04` section 13);
2. the user invoked the command explicitly and asked for repository writes by name (`--repositories`, ADR-0049) — never on discovery, never on a timer, and never as a side effect of deploying to agent clients;
3. a diff is shown before anything is written;
4. ownership and content hash are recorded (`core/04` section 25);
5. an existing file receives a delimited managed block, never a wholesale overwrite, and unrelated content is preserved;
6. an unmapped or third-party repository is never written to at all.

**A file Never4gA creates whole is covered by this exception** (ADR-0011, amended 2026-08-23). Milestone 4's repository marker — `.never4ga.toml`, a workspace UUID and nothing else — is created rather than merged into anything, so conditions 1, 2, 3, 4 and 6 bind unchanged while condition 5, which governs the modification of an existing file, has nothing to constrain. Condition 5 exists to stop Never4gA clobbering content it did not author; a file it creates and wholly owns contains none. Once such a file exists on disk it is an existing file, and condition 5 applies in full.

Nothing else in this section is relaxed. Canonical vault writes remain confined to the configured vault path.

The service must defend against path traversal in file operations.

---

# 8. External URLs

Adapters only connect to configured providers.

Never4gA should not act as an arbitrary HTTP proxy for agents.

---

# 9. Logging

Operational logs must redact:

- Authorization headers;
- API tokens;
- cookies;
- OAuth secrets;
- local service credentials.

Debug mode must not casually disable redaction.

---

# 10. Backups

Canonical vault backup is independent of Never4gA runtime.

Local-only configuration should have an explicit export/backup method that excludes or separately handles secrets.

Derived indexes do not need backup.

---

# 11. Multi-User Future

A future team/hosted edition requires a new threat model:

- user authentication;
- workspace authorization;
- tenant isolation;
- remote API security;
- OAuth;
- audit controls.

Do not prematurely implement that complexity in personal v0.1.
