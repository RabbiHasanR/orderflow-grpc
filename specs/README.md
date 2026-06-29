# Specs — index

Per-feature specs are the **source of truth for WHAT to build and WHY**. Each
capability is one file: `specs/NNN-kebab-name.md`. The system-wide big picture
stays in [architecture.md](../architecture.md), [decisions.md](../decisions.md),
and [workflow.md](../workflow.md) — specs link to those rather than duplicating
them.

**Read this index first** (it's cheap), then open only the spec you need. That's
the whole point: reconstruct intent from a spec instead of re-scanning the repo.

Status vocabulary: `draft` → `in-progress` → `done` (plus `superseded`).
IDs are zero-padded 3-digit, mirroring the `D-NNN` scheme in `decisions.md`.

| ID  | Title              | Service          | Status      | Summary                                              |
|-----|--------------------|------------------|-------------|------------------------------------------------------|
| [001](001-reserve-stock.md) | Reserve stock RPC  | inventory-service | done        | All-or-nothing `ReserveStock` over gRPC + service layer |
| [002](002-order-creation.md) | Order creation flow | order-service    | in-progress | `POST /orders` fans out to `ReserveStock`, then persists |

<!-- Next free ID: 003 -->
