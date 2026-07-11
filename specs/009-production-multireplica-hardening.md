---
id: 009
title: Production hardening for multi-replica scaling
service: both
status: in-progress
created: 2026-07-11
updated: 2026-07-11
related: [architecture.md, "workflow.md", "proto/order_inventory.proto", "002-order-creation.md", "005-bulk-reserve-stream.md", "decisions.md#d-026", "decisions.md#d-036", "decisions.md#d-040", "decisions.md#d-041", "decisions.md#d-042", "decisions.md#d-043"]
---

## Context / Why

The project targets **N order-service + M inventory-service replicas** under
client-side round-robin (D-026), but three gaps would break that in production:

1. **Migrations ran in every container entrypoint** → 2+ replicas race
   `migrate` / `alembic upgrade` on the shared DB at boot.
2. **No idempotency** → a client retry after a timeout invents a new `order_ref`
   and double-reserves stock (oversell). No reservation **release** → a crash
   between reserve-success and order-persist strands stock forever (the D-036 /
   spec 002 "orphaned reservation" gap).
3. **No real health signal / retries / keepalive** → the round-robin LB can't
   route around a sick replica; a cycling replica surfaces `UNAVAILABLE` to the
   user; idle long-lived `WatchStock` streams get dropped by NAT/LB timeouts.

This spec covers closing all three (plan Phases 1–3). Deployment target is
docker-compose `--scale` (not Kubernetes).

## Requirements

**Migrations (Phase 1)**
- Migrations SHALL run exactly once per deploy, in a dedicated one-shot service,
  never in an app replica's entrypoint. App replicas SHALL wait on it via
  `depends_on: { condition: service_completed_successfully }`.

**Idempotency + release (Phase 2)**
- `ReserveStock` SHALL accept an `idempotency_key`; a retry with the same key
  SHALL replay the original outcome without decrementing stock again.
- The DB SHALL enforce, as a hard backstop, at most one **active** (`RESERVED`)
  reservation per `(idempotency_key, product)`.
- A new `ReleaseStock` RPC SHALL return a reserved order's stock and be
  idempotent (releasing nothing-reserved is a no-op success).
- `POST /orders` SHALL accept an `Idempotency-Key` header (≤128 chars), use it as
  the order id, short-circuit to the existing order on repeat, and SHALL call
  `ReleaseStock` to compensate when it fails to persist after a successful reserve.
- `reserve_stock` SHALL lock product rows in a deterministic order (by
  `product_id`) to avoid cross-order deadlocks.

**Resilience (Phase 3)**
- inventory SHALL register the `grpc.health.v1` health service, report `SERVING`,
  and flip to `NOT_SERVING` on graceful shutdown; the container probe SHALL query
  it (not a bare TCP connect). Health SHALL be exempt from auth.
- The client SHALL retry `ReserveStock`/`ReleaseStock` on `UNAVAILABLE` (safe
  because they are idempotent) and SHALL keepalive-ping so idle streams survive.
- order-service SHALL expose `/readyz` (DB + channel) returning `503` when a
  dependency is down; `order-service` SHALL wait for inventory `service_healthy`.

## Design

- **One-shot migrate services** `inventory-migrate` (`manage.py migrate`) and
  `order-migrate` (`alembic upgrade head`) reuse each app's built image via a
  shared `image:` tag and override `command`; both entrypoints are now pure
  `exec "$@"` handoffs. (D-041)
- **Idempotency:** `StockReservation.idempotency_key` + a **partial** unique
  index scoped to `status=RESERVED` and non-empty keys (so a released key can be
  reserved again, and keyless single-shot reservations never collide). `reserve_stock`
  replays on an existing active key; a racing duplicate that slips past the replay
  check hits the unique index → `IntegrityError` → clean failure (no oversell),
  and the caller's retry replays the winner. `order_ref = idempotency_key = order
  id`, so the order layer is idempotent too. (D-040)
- **Release:** `release_stock(order_ref)` locks the order's `RESERVED` rows, adds
  each quantity back with an atomic `F()` update, and marks rows `RELEASED` (audit
  trail). order-service compensates on persist failure and for bulk `partial`
  orders (closes the D-036 gap). (D-040)
- **Health/retry/keepalive:** `grpc.health.v1.HealthServicer` with its own tiny
  pool; `python -m grpc_server.healthcheck` probe; client `retryPolicy` in the
  service config + `grpc.enable_retries`; client keepalive (30s) with matching
  server ping-permit options; `/readyz` on FastAPI. (D-042)
- **nginx edge:** order-service drops its published port and becomes internal;
  an `nginx` service (`nginx/nginx.conf`) is the single published `:8000` edge and
  L7 round-robins across order-service replicas, resolving them at runtime via
  Docker DNS (variable `proxy_pass`). WS + NDJSON streaming pass through
  (upgrade headers, `proxy_buffering off`). This unblocks `--scale order-service=N`. (D-043)

## Tasks

- [done] Phase 1 — one-shot `*-migrate` services; entrypoints reduced to `exec`
- [done] Phase 2a — `idempotency_key` in proto + regenerated stubs
- [done] Phase 2a — model field + partial unique index (migration 0004) + replay
- [done] Phase 2a — `Idempotency-Key` header threaded through `create_order`
- [done] Phase 2b — `ReleaseStock` RPC + `release_stock` (F() restore, RELEASED)
- [done] Phase 2b — compensate on persist failure + bulk `partial` release
- [done] Phase 2c — deterministic lock ordering in `reserve_stock`
- [done] Phase 3 — gRPC health service + real probe (auth-exempt)
- [done] Phase 3 — client retry policy + keepalive (+ server ping-permit)
- [done] Phase 3 — `/readyz` + compose `service_healthy` gating
- [done] Phase 3 — nginx single edge; order-service internal → `--scale order-service=N` (D-043)
- [done] Verify end-to-end with `docker compose up --build --scale inventory-service=3`
- [todo] (later) Phases 4–6: observability, security/config hardening, tests

## Changelog
- 2026-07-11 — created; implements plan Phases 1–3 (migrate job, idempotency +
  release, health/retry/keepalive). Adds D-040/D-041/D-042. Supersedes the spec
  002 / D-036 "orphaned reservation" known-gap. Verified with `--scale
  inventory-service=3` (migrate once, all Healthy, idempotent decrement, release,
  round-robin 3/3/4).
- 2026-07-11 — added nginx as the single HTTP edge (D-043): order-service is now
  internal and L7-load-balanced, so `--scale order-service=N` works behind one URL.
