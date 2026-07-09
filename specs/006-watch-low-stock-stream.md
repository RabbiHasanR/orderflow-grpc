---
id: 006
title: Watch low stock (server-streaming)
service: both
status: in-progress
created: 2026-07-09
updated: 2026-07-09
related: [architecture.md, "decisions.md#d-037", "proto/order_inventory.proto", "specs/005-bulk-reserve-stream.md", "inventory-service/grpc_server/servicer.py", "order-service/app/modules/inventory/service.py"]
---

## Context / Why

The unary `ReserveStock` ([001](001-reserve-stock.md)) and client-streaming
`ReserveStockBulk` ([005](005-bulk-reserve-stream.md)) cover `one-in → one-out`
and `many-in → one-out`. This capability adds the third gRPC shape —
**server streaming** (`one-in → many-out`) — as an independent, read-only RPC:
`WatchLowStock` streams every product at or below a stock threshold. It exists to
exercise/learn the streaming mode and to model a realistic replenishment/alerting
query, while leaving the reservation write-path untouched. See
[D-037](../decisions.md#d-037).

## Requirements

- The system SHALL expose a server-streaming RPC
  `InventoryService.WatchLowStock(LowStockQuery) → stream ProductStock`.
- `LowStockQuery` SHALL carry `threshold` (inclusive upper bound on
  `available_quantity`) and an optional `sku_prefix` filter.
- The server SHALL yield one `ProductStock` per matching product, ordered by SKU,
  pulled lazily from the DB cursor (no full-result buffering).
- An empty result SHALL be a valid **zero-message** stream, NOT an error.
- The server SHALL stop iterating promptly if the client cancels or its deadline
  fires (`context.is_active()`), and SHALL reserve gRPC error statuses for
  malformed requests only (negative `threshold` → `INVALID_ARGUMENT`).
- order-service SHALL expose `GET /inventory/low-stock?threshold=&sku_prefix=` and
  re-stream the results as **NDJSON** (`application/x-ndjson`), one product per
  line, without buffering.
- A gRPC failure *before* the first row SHALL map to an HTTP status; a failure
  *mid-stream* SHALL end the response (status already sent) and be logged.
- The existing reservation RPCs and endpoints SHALL be unchanged.

## Design

- **Contract** ([order_inventory.proto](../proto/order_inventory.proto)): new
  `LowStockQuery` + `ProductStock`; existing messages untouched.
- **Server** ([servicer.py](../inventory-service/grpc_server/servicer.py)):
  `WatchLowStock` is a **generator** that `yield`s `ProductStock`, delegating to a
  new service-layer `stream_low_stock(threshold, sku_prefix)`
  ([services.py](../inventory-service/inventory_app/services.py)) that filters
  `Product` and iterates with `.iterator()` (cursor-backed, flat memory). Sync
  `ThreadPoolExecutor` server unchanged (D-021).
- **Client** ([client.py](../order-service/app/grpc_client/client.py)):
  `watch_low_stock(threshold, sku_prefix)` is an **async generator** — the stub
  call returns an async iterator (not an awaitable), which it re-yields.
- **REST edge** ([inventory/service.py](../order-service/app/modules/inventory/service.py)):
  `InventoryService.stream_low_stock` **probes the first message** inside
  `try/except` (so a pre-stream failure becomes an HTTP status), then returns a
  `StreamingResponse` that emits NDJSON lazily. New read-only module
  `app/modules/inventory/` mounted at `/inventory`.
- **DRY:** gRPC→HTTP status mapping extracted to
  [grpc_client/errors.py](../order-service/app/grpc_client/errors.py); the shared
  `get_inventory` dependency moved to
  [grpc_client/deps.py](../order-service/app/grpc_client/deps.py) (used by both
  the orders and inventory routers).
- **gRPC concept:** server streaming lets one request drive many responses over a
  single call, with the server producing lazily and the client consuming
  incrementally — constant memory end-to-end, versus returning one large list.

## Tasks

- [done] proto: `WatchLowStock` + `LowStockQuery`/`ProductStock`; regenerated stubs both sides
- [done] server: `stream_low_stock` service fn + `WatchLowStock` generator servicer
- [done] client: `watch_low_stock` async-generator method
- [done] REST: `inventory` module (schema/service/router), NDJSON `GET /inventory/low-stock`
- [done] client + server interceptors made unary-stream aware (see below)
- [done] DRY: shared `grpc_to_http_status` + `get_inventory` dependency
- [done] D-037 recorded (server-streaming choice + NDJSON re-stream + error split)
- [todo] end-to-end verification via `docker compose` (populated + empty result + bad auth)

## Interceptor note (third RPC kind)

Adding server streaming exposed the same per-kind limitation each new shape has:

- **Server** ([interceptors.py](../inventory-service/grpc_server/interceptors.py)):
  the auth abort-handler and logging wrapper handled unary-unary + stream-unary
  only; both now branch on `handler.unary_stream`. Logging needed a **stream-aware**
  wrapper — a server-streaming handler returns lazily, so latency/message-count are
  only known once the stream drains; the wrapper iterates and logs on completion.
- **Client** ([interceptors.py](../order-service/app/grpc_client/interceptors.py)):
  a grpc.aio channel buckets interceptors by kind, so we register a third
  `UnaryStreamAuthInterceptor` to attach the token on the server-stream call.

## Changelog

- 2026-07-09 — created with the server-streaming `WatchLowStock` RPC and NDJSON
  `GET /inventory/low-stock`; new `inventory` module; client+server interceptors
  made unary-stream aware; shared gRPC→HTTP mapping and `get_inventory` dependency.
