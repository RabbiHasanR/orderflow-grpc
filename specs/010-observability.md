---
id: 010
title: Observability across replicas (logs, correlation IDs, metrics)
service: both
status: draft
created: 2026-07-11
updated: 2026-07-11
related: [architecture.md, "009-production-multireplica-hardening.md", "inventory-service/grpc_server/interceptors.py", "order-service/app/main.py"]
---

## Context / Why

This is **Phase 4** of the multi-replica production plan (spec
[009](009-production-multireplica-hardening.md) covered Phases 1–3). With N
order-service + M inventory-service replicas behind nginx and gRPC round-robin,
you can no longer debug from a single log stream: a request hops REST → gRPC and
lands on *some* replica. You need to (a) correlate one request across services and
replicas, and (b) see aggregate rates/latency/errors, not just log lines.

Today: unstructured `logging.basicConfig` text on both sides
([order main.py](../order-service/app/main.py), [inventory server.py](../inventory-service/grpc_server/server.py)),
no correlation id, no metrics, no tracing.

## Requirements

- Both services SHALL emit **structured JSON logs** (one object per line) instead
  of the current text format.
- The system SHALL generate/accept an **`X-Request-ID`** at the FastAPI edge (nginx
  can also inject one), forward it as gRPC **metadata** to inventory, and include it
  in every log line on both sides — so one request is greppable end-to-end.
- Both services SHALL expose **Prometheus metrics**: per-RPC request count, latency
  histogram, and error count (by status). order-service SHALL expose `/metrics`.
- The system SHALL expose a **watch-ceiling gauge** on inventory: number of active
  `WatchStock` streams and open `LISTEN` connections, so an alert can fire before
  the `GRPC_MAX_WORKERS`/Postgres-connection ceiling is hit (the monitoring we chose
  over the async-watch refactor).

## Design

- **Logging:** a small JSON formatter wired into `logging` in both entrypoints;
  drop `basicConfig`. Keep the existing logger names (`order.api`, `inventory.grpc`).
- **Correlation id:** a FastAPI middleware mints/propagates `X-Request-ID`; the
  order gRPC **client interceptor** (extend
  [interceptors.py](../order-service/app/grpc_client/interceptors.py)) attaches it as
  metadata; the inventory server **LoggingInterceptor**
  ([interceptors.py](../inventory-service/grpc_server/interceptors.py)) reads it and
  binds it to the log record. This reuses the existing interceptor seams — no new
  transport wiring.
- **Metrics:** `prometheus_client`. Server-side, wrap counts/latency in the existing
  `LoggingInterceptor` (it already times every RPC — natural seam). Client-side, a
  small metrics interceptor + a `/metrics` route on FastAPI. Inventory can run a
  `start_http_server` on a side port, or expose via a tiny handler.
- **Watch gauge:** increment/decrement around the `WatchStock` generator body in the
  [servicer](../inventory-service/grpc_server/servicer.py) and around
  `listen_stock_changes` connections.
- Optional stretch: OpenTelemetry tracing across the REST→gRPC hop (spans linked by
  the same request id).

## Tasks

- [todo] JSON structured logging on both services
- [todo] `X-Request-ID` middleware + propagate via gRPC metadata + log it both sides
- [todo] Prometheus metrics (RPC count/latency/errors) via the interceptor seams
- [todo] `/metrics` endpoint on order-service; metrics exposure on inventory
- [todo] watch-ceiling gauge (active streams + LISTEN connections)
- [todo] (stretch) OpenTelemetry traces across the hop
- [todo] add `prometheus_client` (+ any OTel deps) to requirements

## Changelog
- 2026-07-11 — drafted from the approved multi-replica plan (Phase 4), deferred
  after Phases 1–3 shipped in spec 009.
