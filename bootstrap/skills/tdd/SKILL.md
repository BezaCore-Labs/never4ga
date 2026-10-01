---
name: tdd
description: Implements Never4gA changes test-first with unit, contract, integration, fixture, and retrieval-eval coverage appropriate to the change.
---

# Test-Driven Development

For every behavior:

1. translate specification acceptance criterion into a test;
2. run it and confirm failure for the intended reason;
3. implement the smallest correct behavior;
4. rerun focused tests;
5. refactor;
6. run broader tests.

Backend work requires reusable contract tests.

Do not treat mocks as proof that SQLite/OpenProject/MCP behavior works when a real local integration test is practical.

Do not require live destructive external systems in default CI.

Prefer temporary filesystem vaults and real SQLite for integration tests.
