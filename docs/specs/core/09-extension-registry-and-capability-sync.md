---
type: documentation
id: 01a03502-3e5e-7361-81cf-6f842506b0fb
schema: never4ga/0.1
title: "Never4gA Extension Registry & Capability Sync Contract v0.1"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/09_EXTENSION_REGISTRY_AND_CAPABILITY_SYNC.md"
---
# Never4gA Extension Registry & Capability Sync Contract v0.1

**Status:** Normative extension architecture  
**Purpose:** Manage portable Skills, MCP servers, provider plugins, hooks, and tool-specific capabilities without pretending all agent ecosystems are identical.

---

# 1. Problem

Claude Code, Codex, Antigravity, and future tools have overlapping but different extension systems.

Examples:

```text
Skills
MCP servers
plugins
hooks
rules
subagents
commands
provider-native integrations
```

Manual configuration creates drift.

Blind synchronization creates security and compatibility problems.

Never4gA needs a controlled middle layer.

---

# 2. Canonical vs Provider-Owned

Never4gA distinguishes:

## Canonical portable capability

Owned/source-controlled by Never4gA.

Examples:

```text
portable Agent Skill
approved MCP server declaration
Never4gA standard
```

## Registered provider-native capability

Installed/owned by a particular tool.

Examples:

```text
Claude marketplace plugin
Antigravity plugin
provider-bundled Skill
provider-specific hook
```

Never4gA may know about it without copying it everywhere.

---

# 3. Extension Classes

The registry recognizes at least:

```text
skill
mcp_server
plugin
hook
rule
subagent
command
memory_provider
work_management_provider
storage_backend
context_signal_provider
```

A future extension may add classes.

---

# 4. Registry Location

Canonical human-readable registry definitions belong under:

```text
50_System/Integrations/
```

Recommended structure:

```text
50_System/Integrations/
├── index.md
├── MCP/
├── Plugins/
├── Memory/
└── Providers/
```

Portable Skills remain canonical under:

```text
50_System/Skills/
```

Local credentials and machine paths remain outside the vault.

---

# 5. Extension Record

Conceptually:

```yaml
type: integration
title: OpenProject MCP
provider_kind: mcp_server

capability_id: openproject-mcp

scope: global

clients:
  claude_code: enabled
  codex: enabled
  antigravity_cli: enabled

deployment:
  mode: managed

connection: openproject_mcp
```

Exact schema is finalized during implementation/spec iteration.

No credentials appear here.

---

# 6. Deployment Modes

## `managed`

Never4gA owns/generated the target configuration.

Safe to synchronize/remove if ownership proof exists.

## `adopted`

Originally external/provider-native; user intentionally imported portable components into Never4gA ownership.

## `registered`

Never4gA records that the capability exists, but does not manage files/configuration.

## `provider_native`

Explicitly tool-specific.

Never4gA does not attempt cross-tool synchronization.

---

# 7. Capability Decomposition

A plugin may contain several components.

Example:

```text
Claude/Antigravity plugin
├── Skills
├── MCP definition
├── hooks
├── rules
└── provider-specific features
```

Never4gA may:

```text
adopt portable Skill
adopt MCP declaration
leave hook provider-specific
leave unsupported subagent provider-specific
```

Do not treat the plugin as indivisible when portable components can be safely extracted.

Do not automatically extract without review.

---

# 8. Skill Marketplace / External Installation Workflow

Example:

```text
user installs Claude marketplace plugin
       ↓
provider owns it
       ↓
Never4gA may discover/register it
       ↓
user chooses "adopt portable components"
       ↓
review:
  Skill source
  scripts
  license
  MCP definitions
  dependencies
       ↓
approved Skill copied into
50_System/Skills/
       ↓
adapter sync to selected clients
```

Installing in one provider does NOT automatically mean install everywhere.

---

# 9. MCP Registry

Never4gA should define provider-neutral MCP server intent.

Canonical declaration stores:

```text
name
transport intent
scope
command/server URL metadata where safe
required environment/secret references
enabled clients
permissions/capability notes
ownership mode
```

Local configuration supplies:

```text
secrets
absolute machine paths
tokens
private URLs when intentionally local-only
```

Adapters translate to each client's native configuration.

---

# 10. MCP Synchronization

Conceptual:

```text
Never4gA MCP Registry
          │
    adapter sync
          │
 ┌────────┼────────┐
 ▼        ▼        ▼
Claude   Codex   Antigravity
native   native    native
config   config    config
```

The exact provider config format remains adapter-owned.

Never4gA does not invent one config file and symlink it everywhere.

---

# 11. Provider Configuration Ownership

Adapters keep an ownership manifest.

Never4gA MUST:

- preserve unmanaged MCP servers;
- preserve unmanaged plugins/Skills;
- detect conflicting names;
- show diff before destructive changes;
- refuse silent overwrite;
- remove only entries proven to be Never4gA-managed.

---

# 12. Discovery

A future adapter may mechanically discover installed capabilities.

Examples:

```text
Claude plugin list / filesystem
Codex Skill directories / config
Antigravity plugin directories / MCP config
```

Discovery output is local derived state.

It does not automatically become canonical.

---

# 13. Adoption

Explicit adoption converts:

```text
external installed capability
```

into:

```text
reviewed Never4gA-owned portable source/config
```

where appropriate.

Adoption records:

```text
origin
provider
source URL/repository if known
version/commit if known
license
review date
hash
components adopted
components left provider-native
```

---

# 14. Permissions

MCP/plugin capabilities expand what an agent can access/do.

The registry SHOULD describe:

```text
read-only
writes external state
filesystem access
network access
executes commands
credential requirement
data scope
```

Sync tooling should show this information before enabling a capability on another client.

---

# 15. Tool-Specific Features Are Allowed

Never4gA is not trying to erase provider differences.

It is valid to use:

```text
Claude-only plugin
Antigravity-only hook
Codex-only feature
```

Never4gA records these as provider-native.

Portable core workflows should avoid requiring them unless the workspace explicitly declares that dependency.

---

# 16. Built-in Provider Skills

Provider-bundled Skills may remain provider-native.

Never4gA does not need to vendor every built-in Skill.

If a cross-tool equivalent is required, either:

- adopt/open-source portable source if licensing permits;
- write a Never4gA equivalent;
- declare a provider capability requirement.

---

# 17. External MCP vs Never4gA MCP

Agents may have multiple MCP servers:

```text
Never4gA MCP
OpenProject MCP
GitHub MCP
database MCP
etc.
```

Never4gA MCP is the canonical context/memory/system interface.

External MCPs are capabilities.

Never4gA may optionally centralize their deployment declarations, but it does not need to proxy every external MCP through itself.

This avoids creating a bottleneck and preserves native tool behavior.

---

# 18. When to Proxy an External Capability

Proxy through Never4gA only when there is a real architectural benefit.

Examples:

- normalized PM abstraction across providers;
- canonical memory policy;
- security/policy layer;
- cross-tool semantic normalization;
- cache/provenance requirements.

Otherwise direct client → MCP server is valid.

---

# 19. Extension Portability Levels

Never4gA may classify:

```text
portable
adaptable
provider_native
unknown
```

## `portable`

Same source works across supported tools.

## `adaptable`

Same capability can be translated through adapters.

## `provider_native`

Depends on one provider.

## `unknown`

Not yet reviewed.

---

# 20. Drift Detection

`never4ga adapters doctor` should eventually report:

```text
canonical Skill changed but client copy stale
managed MCP entry missing
managed plugin modified locally
provider-native plugin discovered
name collision
unsupported capability
secret missing
adapter path changed
```

---

# 21. Never4gA Extension Registry Is Not a Marketplace

The first implementation is a personal governance/synchronization layer.

Do not build:

```text
public marketplace
ratings
remote arbitrary installs
automatic update ecosystem
```

until the core is mature.

---

# 22. Future Public Product

A later product may expose:

```text
extension catalog
verified Skill packs
provider adapters
MCP templates
memory providers
work-management providers
```

but the personal architecture must not depend on a central marketplace.

---

# 23. Acceptance Tests

### Unmanaged preservation

Sync never deletes an unmanaged Skill/MCP config.

### Portable Skill

One canonical Skill can deploy to supported client locations.

### Provider-native plugin

A Claude-only plugin may be registered without Never4gA attempting to install it in Codex.

### Adopted Skill

A reviewed Skill extracted from an external plugin becomes canonical and can deploy elsewhere.

### MCP normalization

A generic MCP declaration can be rendered by multiple adapters without exposing credentials in the vault.

### Capability warning

A write-capable MCP server is identified as higher risk before cross-client enablement.

### Client removed

Removing one agent client does not alter canonical extension records.

---

# 24. Implementation Timing

Create the registry data model/ports during agent-adapter work.

Initial actual automation should remain narrow:

```text
Never4gA bootstrap
Never4gA portable Skills
Never4gA MCP server
```

Third-party discovery/adoption and generic MCP synchronization can follow once adapter ownership safety is proven.
