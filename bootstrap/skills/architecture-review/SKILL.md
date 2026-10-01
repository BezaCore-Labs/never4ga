---
name: architecture-review
description: Reviews Never4gA changes for canonical-data boundaries, backend lock-in, context efficiency, memory portability, extension safety, security, and future vector/graph migration risks.
---

# Architecture Review

Check every architectural change against:

## Canonical truth

- Is durable information still represented in Markdown?
- Is a derived database/memory/provider becoming authoritative accidentally?

## Identity

- Are stable Never4gA UUIDs used across boundaries?
- Is any SQLite/Qdrant/Neo4j/Mem0/Zep/provider-native ID leaking into canonical references?

## Mechanical-first context

- Could scope/filter/search/state inspection be deterministic?
- Is an LLM being used to do work FTS/metadata/Git/API/graph traversal can do?
- Is startup bounded?
- Does each Context Pack item have a reason/provenance?
- Does no-LLM mode still work?

## Backend portability

- Does core logic depend on FTS5 SQL, Cypher, Qdrant filters, or pgvector SQL?
- Could a fake backend satisfy the same interface?
- Are provider-specific features behind capabilities/extensions?

## Vector future

- Are embeddings derived?
- Is model/dimension/version recorded?
- Can vectors be rebuilt without canonical changes?

## Graph future

- Are typed relations canonical in Markdown?
- Is graph DB state rebuildable?
- Are graph semantics distinct from graph-provider syntax?

## Memory future

- Does native memory remain usable if external provider is removed?
- Are external memory results clearly derived/candidate?
- Can important memory be promoted to canonical Markdown?
- Are privacy/ingestion boundaries explicit?

## Extension ecosystem

- Is Never4gA overwriting unmanaged Skill/MCP/plugin configuration?
- Is provider-native functionality being falsely treated as portable?
- Does cross-client sync require explicit ownership/adoption?
- Are credentials kept out of canonical files?

## Agent/tool portability

- Does design require one AI vendor?
- Does it proxy paid inference unnecessarily?

## Security

- Are secrets outside vault?
- Are writes classified/authorized?

## Simplicity

- Is added infrastructure required now?
- Could the current milestone succeed without it?
