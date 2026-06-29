---
id: 002
title: Order creation flow
service: order-service
status: in-progress
created: 2026-06-29
updated: 2026-06-29
related: [architecture.md, "workflow.md", "proto/order_inventory.proto", "001-reserve-stock.md"]
---

## Context / Why

A single `POST /orders` REST call must **fan out** over gRPC to inventory's
`ReserveStock` (spec [001](001-reserve-stock.md)) and only persist the order if
the reservation succeeds. The two services own **separate databases** — there is
no shared transaction; consistency is coordinated by the reservation result, not
a foreign key. order-service is currently a stub; this spec covers building it.

## Requirements

- The system SHALL expose `POST /orders` on the only host-published port
  (`order-service:8000`), accepting `{ items: [{ product_id, quantity }] }`.
- The system SHALL validate the body with Pydantic and return `422` on malformed
  input **before** any gRPC or DB work.
- The system SHALL generate the order id up front and pass it as `order_ref`
  (architecture's happy path persists the order *after* a successful reservation).
- The system SHALL call `ReserveStock` as a gRPC client over a `round_robin`
  channel configured with **both** replicas (`inventory-service-1:50051`,
  `inventory-service-2:50051`) — client-side load balancing, no proxy.
- The system SHALL attach `GRPC_AUTH_TOKEN` as call metadata via a client-side
  auth interceptor on every outgoing RPC.
- On `success=True` the system SHALL persist `Order` + `OrderItem`(s) and return
  `201` with the order id. On `success=False` it SHALL NOT persist and return
  `409` with per-item reasons.
- The system SHALL map gRPC transport errors to HTTP per the table in
  [workflow.md](../workflow.md#failure-mapping-what-the-client-sees):
  `UNAVAILABLE→503`, `DEADLINE_EXCEEDED→504`, `UNAUTHENTICATED→500/502`.
- The system SHALL use `grpc.aio` so the FastAPI event loop is not blocked.

## Design

- FastAPI app + SQLAlchemy models on its own Postgres (order-db).
- gRPC client built from the generated stubs in `order-service/app/generated/`.
- Mirrors the server-side interceptor pattern from [001](001-reserve-stock.md).
- **Known gap (out of scope for v1):** reservation commits in inventory *before*
  the order is persisted; a failure after commit leaves an orphaned reservation.
  The future fix is a saga/outbox using the existing `StockReservation.RELEASED`
  status. Tracked here so it isn't forgotten.

## Tasks

- [todo] FastAPI app skeleton + Pydantic order schema (`422` on bad body)
- [todo] SQLAlchemy `Order` / `OrderItem` models + order-db wiring
- [todo] gRPC client: round-robin channel over both replicas
- [todo] client-side auth interceptor (attach `GRPC_AUTH_TOKEN`)
- [todo] server-side auth + logging interceptors on inventory (shared with 001)
- [todo] `POST /orders` handler: generate id → reserve → persist-or-map-error
- [todo] failure mapping (`409` / `503` / `504` / `500`)
- [todo] `docker-compose.yml` wiring + verify round-robin across replicas

## Changelog
- 2026-06-29 — created; scoped from workflow.md `[todo]` items. order-service is stub-only today.
