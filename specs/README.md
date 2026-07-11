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
| [008](008-push-stock-updates.md) | Push-based stock updates (LISTEN/NOTIFY) | inventory-service | in-progress | `WatchStock` updates driven by a Postgres `stock_changed` trigger + per-call `LISTEN` instead of polling (D-039) |
| [009](009-production-multireplica-hardening.md) | Production hardening for multi-replica scaling | both | in-progress | One-shot migrate job + idempotent `ReserveStock`/`ReleaseStock` compensation + gRPC health/retry/keepalive + nginx single edge (D-040–D-043); verified end-to-end |
| [010](010-observability.md) | Observability across replicas | both | draft | Phase 4: structured JSON logs, `X-Request-ID` correlation across REST→gRPC, Prometheus metrics, watch-ceiling gauge |
| [011](011-security-config-hardening.md) | Security & config hardening | both | draft | Phase 5: fail-closed auth, required `DJANGO_SECRET_KEY`/`ALLOWED_HOSTS`, `.env.example`, resource limits, TLS at nginx |
| [012](012-automated-tests.md) | Automated tests | both | draft | Phase 6: concurrency/idempotency/release unit tests + compose integration smoke + CI |

<!-- Next free ID: 013 -->
