---
name: spec
description: Create or update a feature spec in specs/. Claude normally does this automatically per the CLAUDE.md "Spec-driven workflow" section; invoke manually only to force a spec scaffold or status refresh.
---

# spec — feature specs in `specs/`

Specs are the **source of truth for WHAT to build and WHY**. One capability =
one file: `specs/NNN-kebab-name.md`. The system-wide big picture stays in
`architecture.md` / `decisions.md` / `workflow.md`; specs link to them, never
duplicate them.

This skill is the **canonical definition** of the format. The CLAUDE.md
convention points here so there is one source of truth (DRY).

## When (normally automatic — no command needed)
- **New capability, no matching spec** → create the next-ID spec and add a row to
  `specs/README.md` *before/with* implementing.
- **Plan or scope changes** → update Requirements/Design/Tasks, bump `updated:`,
  add a `## Changelog` line.
- **Work completes** → flip Tasks to `[done]`, set `status`, update the index row.

## Naming & IDs
- `specs/NNN-kebab-name.md`, zero-padded 3 digits (e.g. `003-auth-interceptor.md`).
- Next free ID is tracked in the `<!-- Next free ID: NNN -->` comment at the
  bottom of `specs/README.md`. Take it, then bump the comment.
- Status: `draft` → `in-progress` → `done` (plus `superseded`).

## File template
```markdown
---
id: NNN
title: <short title>
service: order-service | inventory-service | proto | infra
status: draft
created: YYYY-MM-DD
updated: YYYY-MM-DD
related: [architecture.md, "workflow.md#anchor", "decisions.md#D-NNN", "path/to/file.py"]
---

## Context / Why
<the problem this solves; link to architecture.md sections>

## Requirements
- The system SHALL ...        # EARS-style, testable, one per line

## Design
<approach, data flow, the gRPC/distributed-systems concept, trade-offs>
<link reused functions with file paths>

## Tasks
- [done] ...
- [todo] ...

## Changelog
- YYYY-MM-DD — created; <scope note>
```

## Index row (`specs/README.md`)
```
| [NNN](NNN-name.md) | Title | service | status | one-line summary |
```

## Token rule
Read `specs/README.md` first (cheap), then open only the relevant spec. Prefer
the spec over re-scanning the repo. A significant design choice still gets a
`decisions.md` `D-NNN` entry; the spec links to it.
