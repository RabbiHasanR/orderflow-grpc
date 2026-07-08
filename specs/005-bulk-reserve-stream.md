---
id: 005
title: Bulk reserve stock (client-streaming)
service: both
status: in-progress
created: 2026-07-08
updated: 2026-07-08
related: [architecture.md, "decisions.md#d-036", "proto/order_inventory.proto", "specs/001-reserve-stock.md", "inventory-service/grpc_server/servicer.py", "order-service/app/modules/orders/service.py"]
---

## Context / Why

The unary `ReserveStock` ([001](001-reserve-stock.md)) already covers "one order,
many items, all-or-nothing." This capability adds the second gRPC shape —
**client streaming** (`many-in → one-out`) — as an independent RPC that reserves
lines across **many orders** in a single stream and returns one aggregate
summary. It exists to exercise/learn the streaming mode and to model a realistic
"bulk import" edge, while leaving the unary happy path untouched. Semantics are
**best-effort per item** (contrast: unary is atomic). See [D-036](../decisions.md#d-036).

## Requirements

- The system SHALL expose a client-streaming RPC
  `InventoryService.ReserveStockBulk(stream BulkReserveItem) → BulkReserveSummary`.
- Each streamed `BulkReserveItem` SHALL be self-contained (`order_ref`,
  `product_id`, `quantity`) so one stream can span many orders.
- The server SHALL reserve each line independently in its own transaction; one
  line failing SHALL NOT roll back others (best-effort per item).
- A malformed line (empty `order_ref`) SHALL be recorded as a failed outcome, NOT
  abort the stream.
- The server SHALL return exactly one `BulkReserveSummary`
  (`total`, `reserved_count`, `failed_count`, `results[]`) after end-of-stream.
- order-service SHALL expose `POST /orders/bulk` taking `{ orders[] }`, stream the
  flattened lines, and persist **only orders whose every line reserved**.
- The bulk endpoint SHALL return `200` with a per-order status
  (`persisted` | `partial` | `failed`) — a mixed batch is not an HTTP error.
- The existing unary `POST /orders` and `ReserveStock` SHALL be unchanged.

## Design

- **Contract** ([order_inventory.proto](../proto/order_inventory.proto)): new
  `BulkReserveItem`, `BulkItemOutcome` (echoes `order_ref` for regrouping),
  `BulkReserveSummary`. Existing messages untouched.
- **Server** ([servicer.py](../inventory-service/grpc_server/servicer.py)):
  `ReserveStockBulk` iterates `request_iterator` and **reuses** `reserve_stock`
  ([001](001-reserve-stock.md)) with a one-item list per line — each call is its
  own `transaction.atomic()` + `select_for_update()`, so best-effort semantics
  and concurrency safety come for free. Sync `ThreadPoolExecutor` server unchanged
  (D-021).
- **Client** ([client.py](../order-service/app/grpc_client/client.py)):
  `reserve_stock_bulk(lines)` passes an **async generator** of `BulkReserveItem`
  to the stub; `grpc.aio` client streaming = request is an async iterator, the
  awaited result is the single summary.
- **REST edge** ([service.py](../order-service/app/modules/orders/service.py)):
  `create_orders_bulk` generates `order_ref` per order, flattens to lines,
  regroups `summary.results` by `order_ref`, persists fully-reserved orders in one
  commit.
- **gRPC concept:** client streaming amortizes N reservations over one call/one
  connection and lets the server aggregate — vs. N unary round-trips.

## Tasks

- [done] proto: `ReserveStockBulk` + bulk messages; regenerated stubs both sides
- [done] server: `ReserveStockBulk` servicer reusing `reserve_stock`
- [done] client: `reserve_stock_bulk` async-generator method
- [done] REST: bulk schemas, `create_orders_bulk`, `POST /orders/bulk`
- [done] client + server interceptors made stream-unary aware (see below)
- [done] D-036 recorded (client-streaming choice + orphaned-reservation caveat)
- [done] end-to-end verification via `docker compose` (happy + mixed batch)
- [todo] compensating release for `partial` orders (future — see D-036)

## Interceptor note (grpc.aio gotcha)

Adding a *second* RPC kind exposed a limitation the unary-only code hid:

- **Client**: a grpc.aio channel partitions its interceptors by RPC kind with an
  **elif-chain**, so one object inheriting several `*ClientInterceptor` mixins
  lands in only the first bucket. We now register **one interceptor per kind**
  (`auth_client_interceptors` → unary-unary + stream-unary) so the token is
  attached on the stream too.
- **Server** ([interceptors.py](../inventory-service/grpc_server/interceptors.py)):
  the auth abort-handler and the logging wrapper were unary-unary only; both now
  branch on `handler.stream_unary` so `ReserveStockBulk` is authenticated and
  logged like the unary path.

## Changelog

- 2026-07-08 — created with the bulk client-streaming RPC and `POST /orders/bulk`;
  verified end-to-end (happy path persists all; mixed batch → `persisted`/`partial`/
  `failed`, orphaned reservation observed for `partial`). Made client+server
  interceptors stream-unary aware.
