# Working on Never4gA

This file is the contract for anyone changing this repository, person or AI
agent. `CLAUDE.md` imports it, so Claude Code reads the same thing.

Never4gA is a local knowledge and context system: a Markdown vault is the
canonical store, and context is assembled from it mechanically, before any
model is involved. This repository is the product. It is public.

## Before you start

1. Read the specifications in [`docs/specs/`](docs/specs/). Start with
   `00-spec-index.md`. `core/` is normative and `details/` elaborates.
2. Read the Skills in `bootstrap/skills/`: `spec-compliance` and `tdd` always,
   and `architecture-review` when a change touches the architecture.
3. Know which requirement your change serves, and how a test will show it. If
   you cannot say, you are not ready to write code.

`docs/specs/` is generated from the maintainer's private vault, where the
specifications are canonical. Never edit it here:
`tests/architecture/test_spec_export.py` fails on any edit made in this
repository. To change a specification, open an issue.

Maintainers start from the vault instead. Its startup pack carries project
state (`Context/project-state.md` in the workspace), plans and the decision
record, and the maintainer-side rules that go with them.

When documents conflict, `core/*` wins over the detailed specs, and the
specifications win over anything else in this repository.

## Setup and the gate

Python 3.14 or later is required.

```bash
uv venv --python 3.14 .venv
VIRTUAL_ENV=.venv uv pip install -e '.[dev]'
git config core.hooksPath .githooks
```

The gate, which every pull request runs:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest
```

Useful selections: `pytest -m architecture` (invariants), `pytest -m contract`
(port contract suites), `pytest tests/unit`, `pytest tests/integration`.

Drive the CLI against a scratch vault, never a real one. Derived state lands
outside the vault, under `$XDG_DATA_HOME/never4ga/vaults/<vault-uuid>/`.

```bash
.venv/bin/never4ga --vault /tmp/scratch init
.venv/bin/never4ga --vault /tmp/scratch index
.venv/bin/never4ga --vault /tmp/scratch --json search "some words"
.venv/bin/never4ga --vault /tmp/scratch --json doctor
```

Live OpenProject instances and hosted AI APIs are never required by the tests.
The tests that use them skip unless you opt in.

## Architecture invariants

- **Markdown is canonical.** SQLite, full-text indexes, vectors, graph
  projections, external memory and caches are derived and rebuildable. None may
  become the only copy of a durable fact.
- **Identity is a UUIDv7** in frontmatter `id`, stable across rename and move.
  Backend row ids, point ids and provider ids are never canonical identity
  (`core/06` §3).
- **Context is mechanical first** (`core/07`): scope resolution, metadata,
  document structure, full-text search, relationship traversal, then Git and
  tracker state, all before any model. Startup makes no model call, stays
  within its budget, never loads the whole vault, and says why each item is in
  the pack. Do not use a model for work a lookup can do.
- **Local first** (`core/05`). One process, SQLite and Markdown. The HTTP API
  binds loopback only. The CLI, the HTTP API and MCP are thin interfaces over
  the same services, and none carries business logic of its own. Everything
  must stay useful with Never4gA not running.
- **Derived state and secrets live outside the vault**, at XDG paths resolved
  through `platform_paths`. Never4gA never auto-commits the vault, and never
  assumes it is the only writer of it.
- **Unknown types, profiles and fields are tolerated** and survive a read and
  write unchanged. Validation reports and never repairs (`core/02`).
- **Native memory is canonical** (`core/08`). External memory providers are
  optional, and their results are candidates, never silently authoritative.
- **Never overwrite what Never4gA does not manage**: Skills, MCP servers,
  plugins, hooks or rules (`core/09`).
- **No provider is required.** Never4gA never proxies inference and never needs
  a paid API to reach something the user already has (`core/00`). A user may
  configure their own.

## Layering

```text
errors ← domain ← layout ← schema ← ports ← ┬ context  ┐
                                            └ indexing ┴ ← ┬ services ┬ ← composition
                                                           └ adapters ┘        ↑
                                                                  api ← service, cli
```

- A module imports its own layer and lower ones only.
- `errors`, `domain`, `layout` and `schema` import no third-party package.
- `platform_paths` and `config` are leaves. Only the composition roots read
  `config`.
- `services` and `adapters` never import each other. Services are written
  against ports, and only a composition root picks an adapter.
- The composition roots are `cli`, `service` and `mcp.toolbox`. None may see
  another. `composition` is the one module below them that sees both adapters
  and services, so what they must assemble identically is assembled once.
- `fastapi`, `pydantic` and `starlette` stay in `api`, and `uvicorn` in
  `service`. `ruamel.yaml` stays in `adapters/filesystem`.

`tests/architecture/test_layering.py` enforces this.
`tests/integration/test_composition_parity.py` catches a root that assembles
less than its siblings, which the layering test cannot see.

## Building

Work test first: requirement, failing test, the smallest correct change, the
focused tests, a refactor that changes no behaviour, then the whole suite.

- A port gets one reusable contract suite in `tests/contracts/`. A new backend
  subclasses it with a fixture and does not restate the behaviour.
- Prefer temporary vaults on disk and real SQLite over mocks.
- The specifications describe more than is built. Their presence is not
  permission to build it. `DEFERRED` in
  `tests/architecture/test_milestone_boundary.py` lists what must not exist
  yet, and the suite fails if it appears.

## Dependencies

Do not add one because it is convenient. Before proposing one, set out what it
does, why the standard library and current code are not enough, and what it
costs in security and maintenance. No opaque agent frameworks or runtime
managers.

`tests/architecture/test_layering.py` holds two gates: model SDKs may only be
imported by an optional enrichment adapter (`core/07` §7), and a list of
packages not needed yet fails the suite if one is imported.

## Comments, tests and commit messages

This repository is public, and everything in it is written for a stranger.

- A comment or docstring says what the code does and the constraint it meets.
  Cite a specification section (`core/02` §21.11) where one states the rule.
  Leave out the story of how the code came to be: dates, incidents, who
  decided, ticket numbers. A reference to a decision record, open question,
  work item or milestone fails the gate unless it is quoted as code, as an
  example.
- Test data is invented. No real people, organisations, places, hosts or
  personal details.
- Commit messages and pull request text describe the change and why it
  matters to the product.

## Branching and pushing

Work happens on a branch and lands through a pull request. Never commit or
push to `main`. `.githooks/pre-commit` refuses a commit on `main` and runs the
lint and type checks. If you have already staged changes, `git switch -c` keeps
them.

A push publishes. `.githooks/pre-push` runs `scripts/public_check.py`, which
checks the comments of the Python, configuration and hook files a push
changes, and checks the message and added lines of every commit being pushed,
and the branch name, against the maintainer's list of private markers. That
list lives in the private vault. Without it, only the comment check runs.
Nothing checks a pull request description, so write it with the same care.

`PACKAGE_MANIFEST.md` records the hash and size of each listed document. A
change to a listed document updates its entry in the same commit.
