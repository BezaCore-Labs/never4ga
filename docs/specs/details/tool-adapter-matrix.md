---
type: documentation
id: 01a03502-3e5f-766c-922f-90f7e669c12b
schema: never4ga/0.1
title: "Never4gA Tool Adapter Matrix v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "details"
sources:
  - "never4ga repository, specification/details/TOOL_ADAPTER_MATRIX.md"
---
# Never4gA Tool Adapter Matrix v0.1

> Provider paths/features are adapter data and must be revalidated during implementation.

| Capability | Claude Code | Codex | Antigravity CLI |
|---|---|---|---|
| Global instruction | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | `~/.gemini/GEMINI.md` |
| Global Skill source | `~/.claude/skills/<skill>/SKILL.md` | `$HOME/.agents/skills/<skill>/SKILL.md` | `~/.gemini/config/skills/<skill>/SKILL.md` |
| Repo instruction | `CLAUDE.md` | `AGENTS.md` / override chain | `AGENTS.md` or `GEMINI.md` |
| Repo Skill | `.claude/skills/<skill>/` | `.agents/skills/<skill>/` | `.agents/skills/<skill>/` |
| Skill format | `SKILL.md` | Agent Skills `SKILL.md` | Agent Skills `SKILL.md` |
| Progressive Skill loading | Yes | Yes | Yes |
| MCP | Yes | Yes | Yes |
| Shell/CLI fallback | Yes | Yes | Yes |
| Never4gA model proxy required | No | No | No |

## Adapter rule

Never4gA does not assume these paths forever.

Each adapter must:

1. identify client/surface/version where possible;
2. expose discovered/configured paths as capabilities;
3. preserve user-owned files;
4. manage only Never4gA-marked content;
5. provide a health check;
6. fail safely if the provider changes its layout.

## Important nuance

Antigravity has multiple surfaces and its documentation has used different global Skill paths across those surfaces/versions.

There are at least **three**, not two:

```text
antigravity_cli     the `agy` command-line client
antigravity_2       the Antigravity 2.x desktop application
antigravity_ide     the editor, a separate product again
```

Antigravity 2.x and Antigravity IDE are distinct applications, not two names for one thing: they keep separate configuration under `~/.config/Antigravity` and `~/.config/Antigravity IDE`, and only the latter carries editor markers such as `CachedExtensionVSIXs`.

Treat each as its own adapter capability profile when implementation requires it.

**`antigravity_ide` is out of scope for v0.1, by the maintainer's ruling of 2026-09-01
(work item 944).** It is not installed on this machine, so it cannot be
measured, and `core/04` §9 makes a client path descriptor data measured against
a real installation rather than a value anybody may reason out. The name stays
in the list above because the *distinction* is the finding — a matrix that
silently collapsed three surfaces into two is what this section exists to
prevent — but nothing ships for it and no descriptor is written. It may be
added in a later version, and the first step will be installing it.

Do not make any of these a permanent core constant. This document said
`~/.gemini/antigravity-cli/skills/` until 2026-08-24, when a probe Skill placed
there was discovered by nothing: the global customization root is
`~/.gemini/config/`. The warning was right, and the path it warned about was
already wrong.

## Measured global instruction paths

Verified 2026-08-23; see the workspace's `Research/q-009-spike-result.md`. Adapter data, not schema — revalidate.

| Surface | Global instruction file | Reads `AGENTS.md` natively |
|---|---|---|
| Claude Code 2.1.241 | `~/.claude/CLAUDE.md` | no |
| Codex CLI 0.149.0 | `~/.codex/AGENTS.md` | **yes** |
| Antigravity CLI 1.1.19 | `~/.gemini/GEMINI.md` | no |
| Antigravity 2.9.1 | `~/.gemini/GEMINI.md` | not measured |
| Antigravity IDE | out of scope for v0.1 (944) | out of scope for v0.1 (944) |

Antigravity CLI 1.1.19 keeps its own settings at `~/.gemini/antigravity-cli/settings.json`, and its headless permission model auto-denies anything unmatched by a `tool(target)` allow-rule.

A desktop surface ships no scriptable entry point, so its acceptance test can only be a manual check or a filesystem assertion — never end-to-end behaviour.

## Measured global Skill paths

Verified 2026-08-24, Milestone 5b. Adapter data, not schema — revalidate.

Method: a probe Skill carrying an unguessable token was deployed one mechanism
at a time. A no-tools prompt asked each client to *name* any Skill beginning
`never4ga`, which tests registration; a second prompt asked for the token, which
tests that the body was actually read. Controls with nothing deployed returned
none on all three.

| Surface | Global Skill directory | Copy | Symlink | Body read via symlink |
|---|---|---|---|---|
| Claude Code 2.1.231 | `~/.claude/skills/<skill>/SKILL.md` | yes | yes | yes |
| Codex CLI 0.149.0 | `~/.agents/skills/<skill>/SKILL.md` | yes | yes | yes |
| Antigravity CLI 1.1.19 | `~/.gemini/config/skills/<skill>/SKILL.md` | yes | yes | yes |
| Antigravity 2.9.1 | not measured | | | |
| Antigravity IDE | not measured | | | |

The three paths are isolated. With only `$HOME/.agents/skills/` populated, Codex
found the Skill and the other two reported none; no client reads another's
directory.

Antigravity CLI documents its own layout in a bundled `agy-customizations`
Skill. Its global root is `~/.gemini/config/`; its per-workspace root is
`.agents/` (also `.agent/`, `_agents/`, `_agent/`), walked from the working
directory up to the repository root.

It also has a third deployment mechanism the other two lack: a `skills.json` of
the form `{"entries": [{"path": "<dir>"}]}` mounts an arbitrary directory of
Skills. That is a per-client capability, not a portable one, and belongs in that
client's descriptor rather than in the engine.

### Why copy remains the default

Symlinks work on all three, end to end — which `core/04` section 24 permits
without requiring ("no symlink is required"). Deployment stays **copy** anyway,
and symlink is an opt-in descriptor capability.

A symlinked Skill is a client writing into the Git-backed vault. An agent
editing what it thinks is its own Skill would rewrite the canonical copy for
every client at once, and a vault on an auto-commit timer would carry it to
every remote unreviewed. Section 25 rules 4 and 5 — do not destroy an edit
silently, report drift and require reconciliation — need two copies to compare
before they mean anything. Symlinks do not make that manifest unnecessary; they
remove the containment it exists to provide.

Copy also has no platform question. Symlinks on Windows need developer mode or
elevation, and `core/05` section 7's platform abstraction exists so a second
platform stays possible.
