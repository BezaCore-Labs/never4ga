---
type: documentation
id: 01a03502-3e5b-75de-8d71-9dd78fa07db2
schema: never4ga/0.1
title: "Never4gA — Design Principles / Constitution Draft"
created_at: "2026-08-22T12:00:00-05:00"
workspace: 01a03428-7d75-703a-8b55-58b8d820bbb6
authority: authoritative
tags:
  - specification
  - "core"
sources:
  - "never4ga repository, specification/core/00_CONSTITUTION.md"
---
# Never4gA — Design Principles / Constitution Draft

1. **Markdown is canonical.**
2. **The user owns the knowledge.**
3. **Derived databases and indexes are disposable/rebuildable whenever practical.**
4. **No AI provider owns system state.**
5. **Every external tool is replaceable.**
6. **The system remains useful without Never4gA running.**
7. **Obsidian is a first-class human interface, not the canonical data store.**
8. **Metadata follows a formal documented schema.**
9. **OKF compatibility should be preserved where applicable.**
10. **Folders provide broad physical organization; metadata provides semantic classification.**
11. **Links and relationships should be explicit whenever practical.**
12. **Workspaces are recursively nestable.**
13. **Base workspace semantics are standardized.**
14. **Workspace types extend the base standard rather than redefine it.**
15. **Unknown extension fields/types should generally be tolerated.**
16. **Shared knowledge should not be duplicated unnecessarily.**
17. **AI-generated inference must be distinguishable from user-authored or user-confirmed knowledge.**
18. **Provenance and authority matter.**
19. **Staleness, contradiction, drift, duplicates, and broken structure are first-class maintenance concerns.**
20. **Context should be assembled progressively rather than loading the whole vault.**
21. **Retrieval and memory are distinct subsystems.**
22. **Skills define reusable procedures; project knowledge lives elsewhere.**
23. **Skills should be portable and understandable by the user.**
24. **Third-party skills may be adopted, but not as opaque unmanaged dependencies.**
25. **Software repositories remain separate from the vault.**
26. **External repos may be mapped to workspaces and optionally indexed.**
27. **The core must not proxy model inference or require per-token API use merely to access subscribed AI tools.**
28. **The runtime exposes stable interfaces such as CLI, MCP, and local API.**
29. **Backend implementations such as SQLite, Qdrant, or Neo4j remain replaceable behind abstractions.**
30. **Prefer the simplest design that preserves required architecture.**
31. **The core implementation should be test-driven.**
32. **Never4gA must be bootstrappable using normal Claude Code/Codex sessions before Never4gA itself is complete.**
