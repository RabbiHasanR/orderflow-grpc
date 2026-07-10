---
id: 007
title: Live stock watch (bidirectional-streaming)
service: both
status: in-progress
created: 2026-07-10
updated: 2026-07-10
related: [architecture.md, "decisions.md#d-038", "proto/order_inventory.proto", "specs/006-watch-low-stock-stream.md", "inventory-service/grpc_server/servicer.py", "order-service/app/modules/inventory/service.py"]
---

## Context / Why

Specs 001/005/006 covered three of the four gRPC shapes — unary `ReserveStock`
(`1→1`), client-streaming `ReserveStockBulk` (`many→1`), server-streaming
`WatchLowStock` (`1→many`). This capability adds the fourth and final shape,
**bidirectional streaming** (`many↔many`), as an independent, read-only RPC:
`WatchStock`. The client streams subscribe/unsubscribe commands; the server
independently streams live stock updates for the current watch set.

The point is *decoupled duplex* — the property the other three cannot show: the
request and response streams flow **concurrently**, and a response is **not** 1:1
with a request. A subscribe command doesn't produce "one answer"; it reshapes what
the response stream emits. It is a clean evolution of `WatchLowStock` — a fixed
one-shot query becomes a *mutable live subscription* you steer mid-stream without
reconnecting. Read-only; the reservation write-path is untouched. See
[D-038](../decisions.md#d-038).

## Requirements

- The system SHALL expose a bidirectional-streaming RPC
  `InventoryService.WatchStock(stream WatchCommand) → stream StockUpdate`.
- `WatchCommand` SHALL carry an `action` (`SUBSCRIBE`/`UNSUBSCRIBE`) and
  `product_ids`; the server SHALL maintain a per-call watch set that these
  commands mutate at any time during the call.
- On subscribe the server SHALL emit a `SNAPSHOT` `StockUpdate` for each product;
  thereafter it SHALL emit a `CHANGED` update whenever a watched product's
  `available_quantity` differs from the last value sent, detected on a poll tick
  (`GRPC_WATCH_POLL_SECONDS`, default 2s).
- The two streams SHALL be independent: responses are NOT 1:1 with requests, and
  both SHALL flow concurrently over one long-lived call.
- The call SHALL be unbounded (no per-call deadline). Teardown SHALL be
  client-driven — the server stops when the request stream closes or the client
  cancels (`context.is_active()`).
- Unknown product ids SHALL be ignored (no error); gRPC error statuses stay
  reserved for malformed requests / auth.
- order-service SHALL front the RPC at a WebSocket `WS /inventory/stock-watch`
  (JSON commands in, JSON updates out) and serve a browser demo page at
  `GET /inventory/stock-watch/demo`.
- The existing reservation RPCs and `/inventory/low-stock` endpoint SHALL be
  unchanged.

## Design

- **Contract** ([order_inventory.proto](../proto/order_inventory.proto)): new
  `WatchStock` RPC + `WatchCommand` (with `Action` enum) and `StockUpdate` (with
  `Kind` enum: `SNAPSHOT`/`CHANGED`); existing messages untouched.
- **Server** ([servicer.py](../inventory-service/grpc_server/servicer.py)):
  `WatchStock` is where the core lesson lives. On the sync ThreadPoolExecutor
  server (D-021) one thread cannot both block on `for cmd in request_iterator`
  and poll+`yield` responses, so it spawns a **background reader thread** that
  drains the command stream into a lock-guarded `watch_set` (marking freshly-added
  ids for a snapshot), while the handler's own pool thread polls the watch set
  each tick via a new read-only `fetch_stock(product_ids)`
  ([services.py](../inventory-service/inventory_app/services.py), sharing a
  `_to_row` helper with `stream_low_stock`), emitting `SNAPSHOT`/`CHANGED` and
  tracking `last_seen`. A `stop` event and `context.is_active()` end the loop.
- **Client** ([client.py](../order-service/app/grpc_client/client.py)):
  `watch_stock(commands)` hands the stub an async iterator of `WatchCommand` and
  re-yields each `StockUpdate`. Deliberately **omits** `timeout=self._deadline` —
  a live watch is unbounded (the concrete form of the D-037 deadline caveat).
- **REST edge** ([inventory/service.py](../order-service/app/modules/inventory/service.py)):
  `InventoryService.stock_watch` bridges a WebSocket to the bidi call with **two
  concurrent asyncio tasks** — inbound (socket JSON → validated `WatchCommandIn` →
  `pb2.WatchCommand` onto a queue backing the request stream) and outbound (gRPC
  `StockUpdate` → socket JSON). Whichever side ends first tears the other down.
  New schemas `WatchCommandIn`/`StockUpdateOut`
  ([schemas.py](../order-service/app/modules/inventory/schemas.py)); demo HTML in
  [demo.py](../order-service/app/modules/inventory/demo.py); routes in
  [router.py](../order-service/app/modules/inventory/router.py).
- **Interceptors (fourth kind, `stream_stream`):** server-side auth abort-handler
  + logging gained a `handler.stream_stream` branch (logging reuses `timed_stream`);
  client-side a fourth `StreamStreamAuthInterceptor` was registered.
- **gRPC concept:** bidirectional streaming is one long-lived call carrying two
  independent message streams; either side sends whenever it wants and the streams
  are correlated only by the application logic you write, not by the transport.

## Tasks

- [done] proto: `WatchStock` + `WatchCommand`/`StockUpdate`; regenerated stubs both sides
- [done] server: `fetch_stock`/`_to_row` service fn + `WatchStock` handler w/ reader thread + `GRPC_WATCH_POLL_SECONDS`
- [done] interceptors: server `stream_stream` branches (reuse `timed_stream`); client `StreamStreamAuthInterceptor`
- [done] client: `watch_stock` async-generator (unbounded — no per-call deadline)
- [done] REST: WebSocket `/inventory/stock-watch` bridge + `WatchCommandIn`/`StockUpdateOut`
- [done] demo: `GET /inventory/stock-watch/demo` static HTML page
- [done] D-038 recorded (bidi choice + WebSocket edge + sync-server concurrency + scaling caveat)
- [todo] end-to-end verification via `docker compose` (browser demo: snapshot on subscribe, changed after a reserve, unsubscribe stops updates, tab-close teardown)

## Scaling note (accepted trade-off)

Each active watch holds **two** pool threads (handler + reader) for its lifetime,
bounded by `GRPC_MAX_WORKERS`; `server.stop(grace)` won't force-close long-lived
streams (teardown is client-driven). Fine for a learning/demo scale — a
production system would use an async server and/or a DB change-feed (LISTEN/NOTIFY)
instead of per-watcher polling. See [D-038](../decisions.md#d-038).

## Changelog

- 2026-07-10 — created with the bidirectional-streaming `WatchStock` RPC, the
  WebSocket `/inventory/stock-watch` edge + browser demo page, sync-server reader
  thread, and the fourth (`stream_stream`) interceptor kind on both layers.
