---
id: 002
title: Order creation flow
service: order-service
status: in-progress
created: 2026-06-29
updated: 2026-07-01
related: [architecture.md, "workflow.md", "proto/order_inventory.proto", "001-reserve-stock.md", "decisions.md#d-033", "decisions.md#d-034"]
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

- FastAPI app + async SQLAlchemy (asyncpg, D-030) models on its own Postgres
  (order-db). Schema managed by Alembic, `alembic upgrade head` in the entrypoint
  (D-035, supersedes the original startup `create_all` of D-033). The code layout
  moved to domain modules — see spec [004](004-order-service-restructure.md).
- gRPC client built from the generated stubs in `order-service/app/generated/`,
  over a `grpc.aio` `round_robin` channel (D-034 for the shared auth metadata key).
- Client-side auth interceptor attaches the token; inventory now enforces it with
  a server-side auth + logging interceptor pair (built in this pass, D-034).
- **`Order.total`/pricing is omitted** — the proto carries only `product_id` +
  `quantity`, so there is no price data to total. A future field once a catalog/
  pricing source exists; the `status` column is kept for future `PENDING`→
  `CONFIRMED` saga states.
- **Known gap (out of scope for v1):** reservation commits in inventory *before*
  the order is persisted; a failure after commit leaves an orphaned reservation.
  The future fix is a saga/outbox using the existing `StockReservation.RELEASED`
  status. Tracked here so it isn't forgotten.

## Tasks

- [done] FastAPI app skeleton + Pydantic order schema (`422` on bad body)
- [done] async SQLAlchemy `Order` / `OrderItem` models + order-db wiring
- [done] gRPC client: `round_robin`-ready channel (single replica for now, D-026)
- [done] client-side auth interceptor (attach `GRPC_AUTH_TOKEN`)
- [done] server-side auth + logging interceptors on inventory (D-034)
- [done] `POST /orders` handler: generate id → reserve → persist-or-map-error
- [done] failure mapping (`409` / `503` / `504` / `502`)
- [done] `docker-compose.yml` wiring (order-db + order-service, only 8000 published)
- [todo] add 2nd inventory replica + one-shot migrate job; verify round-robin
  across replicas (D-026 follow-up — channel is already round-robin-ready)

## Changelog
- 2026-06-29 — created; scoped from workflow.md `[todo]` items. order-service is stub-only today.
- 2026-07-01 — order-service built (app, async DB, gRPC client + interceptor);
  inventory server-side auth+logging interceptors added; compose wired with
  order-db + order-service. Decided single replica for now (D-026) with a
  round-robin-ready channel, so round-robin verification is the one remaining
  task. Added D-033 (create_all) and D-034 (shared auth metadata). Status stays
  `in-progress` until the 2nd replica / round-robin demo lands.
