---
type: documentation
id: 01a03502-3e5b-75de-8d71-9dd62dea6172
schema: never4ga/0.1
title: "Never4gA Specification Index"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "index"
sources:
  - "never4ga repository, specification/00_SPEC_INDEX.md"
---
# Never4gA Specification Index

This document defines the normative implementation specifications.

## Normative core

Read these before architectural work:

1. [`core/00_CONSTITUTION.md`](core/00-constitution.md)
2. [`core/01_VAULT_SPECIFICATION.md`](core/01-vault-specification.md)
3. [`core/02_FORMAL_SCHEMA.md`](core/02-formal-schema.md)
4. [`core/03_WORKSPACE_SPECIFICATION.md`](core/03-workspace-specification.md)
5. [`core/04_AGENT_AND_SKILLS_ARCHITECTURE.md`](core/04-agent-and-skills-architecture.md)
6. [`core/05_RUNTIME_ARCHITECTURE.md`](core/05-runtime-architecture.md)
7. [`core/06_FUTURE_BACKENDS_AND_EVOLUTION.md`](core/06-future-backends-and-evolution.md)
8. [`core/07_CONTEXT_ACQUISITION_AND_MECHANICAL_TOOLS.md`](core/07-context-acquisition-and-mechanical-tools.md)
9. [`core/08_MEMORY_PROVIDER_EXTENSION_CONTRACT.md`](core/08-memory-provider-extension-contract.md)
10. [`core/09_EXTENSION_REGISTRY_AND_CAPABILITY_SYNC.md`](core/09-extension-registry-and-capability-sync.md)

## Required detailed specifications

- [`details/DATA_INDEXING_MAINTENANCE.md`](details/data-indexing-maintenance.md)
- [`details/RETRIEVAL_CONTEXT_MEMORY.md`](details/retrieval-context-memory.md)
- [`details/API_CLI_MCP_CONTRACT.md`](details/api-cli-mcp-contract.md)
- [`details/OPENPROJECT_ADAPTER.md`](details/openproject-adapter.md)
- [`details/OBSIDIAN_EXPERIENCE.md`](details/obsidian-experience.md)
- [`details/SECURITY_CONFIGURATION.md`](details/security-configuration.md)
- [`details/SKILL_LIBRARY_POLICY.md`](details/skill-library-policy.md)
- [`details/TOOL_ADAPTER_MATRIX.md`](details/tool-adapter-matrix.md)
- [`details/AGENT_SESSION_FLOW.md`](details/agent-session-flow.md)
- [`details/EXTERNAL_WORK_MANAGEMENT_ARCHITECTURE.md`](details/external-work-management-architecture.md)
- [`details/AGENT_INSTRUCTION_LAYERING.md`](details/agent-instruction-layering.md)

## Milestone 0 — complete

Read for Milestone 0:

- [`core/00_CONSTITUTION.md`](core/00-constitution.md)
- [`core/05_RUNTIME_ARCHITECTURE.md`](core/05-runtime-architecture.md)
- [`core/06_FUTURE_BACKENDS_AND_EVOLUTION.md`](core/06-future-backends-and-evolution.md)
- [`core/07_CONTEXT_ACQUISITION_AND_MECHANICAL_TOOLS.md`](core/07-context-acquisition-and-mechanical-tools.md)
- [`core/08_MEMORY_PROVIDER_EXTENSION_CONTRACT.md`](core/08-memory-provider-extension-contract.md)
- [`core/09_EXTENSION_REGISTRY_AND_CAPABILITY_SYNC.md`](core/09-extension-registry-and-capability-sync.md)
- the workspace's `Plans/` (Implementation Roadmap)
- the workspace's `Plans/` (Milestone 0 Repository Bootstrap)
- root `AGENTS.md`

The purpose of reading future contracts in Milestone 0 was to create correct seams/interfaces, **not** to implement later systems now. That rule still holds for every later milestone.

## Milestone 1 — complete

Read for Milestone 1, in addition to the Milestone 0 list above:

- [`core/01_VAULT_SPECIFICATION.md`](core/01-vault-specification.md)
- [`core/02_FORMAL_SCHEMA.md`](core/02-formal-schema.md)
- [`core/03_WORKSPACE_SPECIFICATION.md`](core/03-workspace-specification.md)
- [`details/OBSIDIAN_EXPERIENCE.md`](details/obsidian-experience.md)
- the workspace's `Plans/` (Milestone 1 Vault Core)

## Operating the vault

The workspace's `Runbooks/vault-bootstrap.md` covers creating and maintaining an actual vault, as
distinct from the code that serves it. Read it before running `never4ga init`
against anything real.

## Milestone 2 — complete

Read for Milestone 2, in addition to everything above:

- [`details/DATA_INDEXING_MAINTENANCE.md`](details/data-indexing-maintenance.md)
- [`details/RETRIEVAL_CONTEXT_MEMORY.md`](details/retrieval-context-memory.md)
- [`core/06_FUTURE_BACKENDS_AND_EVOLUTION.md`](core/06-future-backends-and-evolution.md)
- [`core/07_CONTEXT_ACQUISITION_AND_MECHANICAL_TOOLS.md`](core/07-context-acquisition-and-mechanical-tools.md)
- the workspace's `Logs/` (Milestone 1 Completion Report)
- the workspace's `Plans/` (Milestone 2 Sqlite Index)

## Milestone 3 — complete

Read for Milestone 3, in addition to everything above:

- [`core/05_RUNTIME_ARCHITECTURE.md`](core/05-runtime-architecture.md) sections 9 to 20
- [`details/SECURITY_CONFIGURATION.md`](details/security-configuration.md)
- [`details/API_CLI_MCP_CONTRACT.md`](details/api-cli-mcp-contract.md)
- [`details/DATA_INDEXING_MAINTENANCE.md`](details/data-indexing-maintenance.md) sections 15 to 17
- the workspace's `Plans/` (Milestone 3 Local Service)
- the workspace's `Logs/` (Milestone 3 Completion Report)
- the workspace's `Context/` (Local Service Operation) before running a service against a real vault

## Milestone 4+

Read the specification for each subsystem before implementation.

## Historical/correction handling

The original Phase-B `index.md` manifest idea was corrected during Phase C to preserve OKF semantics.

The consolidated [`core/01_VAULT_SPECIFICATION.md`](core/01-vault-specification.md) incorporates that correction.

Do not revert to `_workspace.md` or use `index.md` as a normal frontmatter-bearing workspace manifest.
