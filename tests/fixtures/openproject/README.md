# OpenProject API v3 fixtures

Captured live from an OpenProject instance and sanitized before being checked
in. These are what CI runs against: the live tests are opt-in and never run by
default, so a change to the adapter is caught here.

## What was captured

| File | Request |
|---|---|
| `root.json` | `GET /api/v3` — `coreVersion`, and the authenticated principal |
| `project.json` | `GET /api/v3/projects/never4ga` |
| `work-packages.json` | the project's work packages, all statuses, one page |
| `work-packages_page-1..3.json` | the same collection at `pageSize=3` |
| `work-package.json` | `GET /api/v3/work_packages/838` |
| `relations_empty.json` | `GET /api/v3/work_packages/838/relations` |
| `statuses.json`, `types.json`, `priorities.json`, `versions.json` | the vocabularies a filter resolves against |
| `not-found.json` | the body of a `404` for an unknown work package |

The three page files are the fixture that matters most. `offset` on an
OpenProject collection is a **1-based page number**, not an element offset, and
the pages here prove it: `offset=1` holds ids 838–840, `offset=2` holds 841–843,
`offset=3` holds the last two of eight. A paginator that added `pageSize` to
`offset` would pass every single-page test and silently re-fetch overlapping
windows in production.

## What is synthesized, and why

Two files are built from a captured shape rather than captured whole:

- `work-package_populated.json` — the instance has no work package with an
  assignee, a responsible, dates or a percentage complete, so normalisation of
  those fields could not be exercised from a live read at all. The document is a
  captured work package with those `_links` and fields filled in the shape
  OpenProject fills them.
- `relations_populated.json` — no relation exists anywhere on the instance. The
  collection envelope is the captured one; the two elements are written to the
  documented `Relation` shape.

Everything else is a live response with only the substitutions below applied.

## Sanitization

Instance host, account and organisation names are replaced, and every work
package's description is invented text of the same shape: Markdown paragraphs
in `raw`, and the paragraphs OpenProject renders from them in `html`. Nothing
else is edited, so the field set, the envelope and the `_links` shape stay
exactly as the server sent them.

```text
the real instance's host -> openproject.example
the maintainer's name   -> Ada Example
the maintainer's login  -> ada
email addresses         -> ada@example.test
gravatar URLs           -> https://avatar.example/anonymous
organisation names      -> Example Org / Example-Org
descriptions            -> invented text, as above
```

No credential was captured: authentication is an `Authorization` header, and
only response bodies are stored here.

## Refreshing them

Re-capture against a live instance and re-apply the substitutions above. Keep
the paging files at `pageSize=3` — the point of them is that the collection does
not fit on one page.
