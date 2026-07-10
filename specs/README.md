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
| [002](002-order-creation.md) | Order creation flow | order-service    | in-progress | `POST /orders` fans out to `ReserveStock`, then persists (built; round-robin demo pending) |
| [003](003-inventory-containerization.md) | Inventory-service containerization | inventory-service | done | Production Dockerfile + Compose slice (inventory-db + inventory-service) |
| [004](004-order-service-restructure.md) | Order-service domain layout + Alembic | order-service | in-progress | Domain-module layout (`core`/`api`/`modules/orders`) + Alembic migrations replacing startup `create_all` (D-035) |
| [005](005-bulk-reserve-stream.md) | Bulk reserve stock (client-streaming) | both | in-progress | Client-streaming `ReserveStockBulk` (many orders, best-effort per item) + `POST /orders/bulk` (D-036) |
| [006](006-watch-low-stock-stream.md) | Watch low stock (server-streaming) | both | in-progress | Server-streaming `WatchLowStock` (low-stock query) re-streamed as NDJSON at `GET /inventory/low-stock` (D-037) |
| [007](007-live-stock-watch-bidi.md) | Live stock watch (bidirectional-streaming) | both | in-progress | Bidirectional `WatchStock` (dynamic subscribe/unsubscribe ↔ live stock updates) fronted at WebSocket `/inventory/stock-watch` + demo page (D-038) |

<!-- Next free ID: 008 -->
