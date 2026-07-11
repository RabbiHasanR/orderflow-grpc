---
id: 013
title: Inventory-service application & stream logging
service: inventory-service
status: in-progress
created: 2026-07-11
updated: 2026-07-11
related: [
  "specs/010-observability.md",
  "inventory-service/inventory_app/services.py",
  "inventory-service/grpc_server/servicer.py",
  "inventory-service/grpc_server/interceptors.py",
  "inventory-service/inventory_project/settings.py",
  "order-service/app/modules/orders/service.py",
]
---

## Context / Why

order-service narrates its domain flow in the log — `order created`, `idempotent
replay`, `ReserveStock failed`, `compensation failed` — so a live tail tells the
story of a request. inventory-service does not: the `LoggingInterceptor` logs one
transport line per RPC (method, peer, status, latency), but the **business layer
emits nothing**, so you can see *"ReserveStock, OK, 4ms"* yet never *"reserved
3×SKU-42, 1 line short-stocked, order abc released"*.

For a learning project the bidirectional `WatchStock` RPC ([007](007-live-stock-watch-bidi.md))
is the most opaque: subscribe/unsubscribe happen **inside** one long-lived stream,
invisible to the per-RPC interceptor. We want to *see* each subscribe/unsubscribe
as it arrives.

This is the plain-stdlib-logging precursor to [010](010-observability.md)'s
structured JSON + request-ID correlation — same named loggers and format, so 010
is later a config swap, not a rewrite.

## Requirements

- The service layer ([services.py](../inventory-service/inventory_app/services.py))
  SHALL log business outcomes on logger `inventory.service`, mirroring
  order-service's level discipline:
  - INFO: successful reserve, idempotent replay, successful release.
  - WARNING: expected failures — reserve rejected (insufficient stock / unknown
    product / non-positive qty), concurrent-duplicate `IntegrityError`.
  - INFO: release no-op (nothing was still `RESERVED`).
- The `WatchStock` RPC ([servicer.py](../inventory-service/grpc_server/servicer.py))
  SHALL log, on logger `inventory.grpc`, each **SUBSCRIBE** and **UNSUBSCRIBE**
  command with peer and product ids, plus stream open and teardown.
- Client-streaming `ReserveStockBulk` SHALL log a one-line summary
  (total / reserved / failed); malformed lines SHALL log WARNING.
- Server-streaming `WatchLowStock` SHALL log the query (threshold, prefix).
- Helper threads in `WatchStock` (`_drain_commands`, `_drain_notifications`) SHALL
  log unexpected exceptions (`logger.exception`) instead of dying silently.
- Logs SHALL NOT contain secrets; no full request payloads — ids/counts only.
- Config: logging SHALL be driven by a Django `LOGGING` dict in
  [settings.py](../inventory-service/inventory_project/settings.py) (single source
  of level+format), with `basicConfig` in `serve()` kept as a fallback.

## Design

- **Two logger namespaces, one format** (matches order-service `%(asctime)s
  %(levelname)s %(name)s: %(message)s`):
  - `inventory.grpc` — transport/stream lifecycle (interceptors + servicer).
  - `inventory.service` — domain events (service layer).
- **Why the split:** the interceptor already owns per-RPC transport lines; the
  service layer owns domain semantics. Keeping them under distinct names lets 010
  route/level them independently later.
- **Watch logging placement:** subscribe/unsubscribe logged inside
  `_drain_commands` (the reader thread) where the command is decoded — this is the
  only place bidi intent is visible, since the interceptor sees just "one long
  stream". Open logged at RPC entry, teardown in the `finally`.
- **Noise control:** `fetch_stock` runs on every watch tick — it stays at DEBUG (or
  unlogged) so a live watch doesn't flood INFO. `stream_low_stock` logs the query
  once at start, not per row.
- **Django `LOGGING` dict:** `disable_existing_loggers: False`; a `console` handler
  to stdout; `inventory` logger at INFO. `serve()` keeps `basicConfig` as a guard
  for the pre-`django.setup()` window.

## Tasks

- [done] `LOGGING` dict in settings.py; keep `basicConfig` fallback in serve()
- [done] `inventory.service` logs in reserve_stock / release_stock (info+warning)
- [done] query log in stream_low_stock
- [done] WatchStock: open/subscribe/unsubscribe/teardown logs (`inventory.grpc`)
- [done] thread-exception logging in `_drain_commands` / `_drain_notifications`
- [done] ReserveStockBulk summary + malformed-line warning (WatchLowStock query logged one layer down in `stream_low_stock`, not duplicated in servicer)
- [todo] verify via `docker compose logs -f` that a subscribe shows a log line

## Changelog
- 2026-07-11 — created; plain-logging precursor to spec 010 observability.
