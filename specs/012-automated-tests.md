---
id: 012
title: Automated tests (concurrency, idempotency, release, integration)
service: both
status: draft
created: 2026-07-11
updated: 2026-07-11
related: [architecture.md, "009-production-multireplica-hardening.md", "inventory-service/inventory_app/services.py", "order-service/app/modules/orders/service.py"]
---

## Context / Why

This is **Phase 6** of the multi-replica production plan. The project currently has
**zero automated tests** (only `grpcio-tools` in `requirements-dev.txt`); verification
has been manual via `docker compose up`. The correctness guarantees added in spec 009
(no oversell, idempotency, release) are exactly the kind of invariant that needs a
regression test so a future change can't silently break them.

## Requirements

- A **concurrency test** SHALL fire N parallel `reserve_stock` calls for the same
  product and assert stock never goes negative and total reserved never exceeds
  starting stock (proves the `select_for_update` lock holds).
- An **idempotency test** SHALL call `ReserveStock` twice with the same
  `idempotency_key` and assert exactly one decrement and identical responses.
- A **release test** SHALL reserve then `ReleaseStock` and assert stock is restored,
  the row is `RELEASED`, and a second release is a no-op.
- An **integration smoke test** SHALL run `docker compose up --scale
  inventory-service=2` and exercise `POST /orders` end-to-end (happy path + a
  duplicate `Idempotency-Key`).
- Tests SHALL run in CI (GitHub Actions).

## Design

- Inventory: `pytest` + `pytest-django` against a test Postgres (the service-layer
  functions in [services.py](../inventory-service/inventory_app/services.py) are
  framework-agnostic and directly unit-testable). Concurrency test uses threads +
  a real DB so the row lock is actually exercised (SQLite won't do).
- order-service: `pytest` + `httpx.AsyncClient` against the FastAPI app with the
  gRPC client mocked (or pointed at a test inventory), covering the
  [create_order](../order-service/app/modules/orders/service.py) idempotency
  short-circuit and the release-on-failure compensation.
- Integration: a compose-based test (testcontainers or a CI compose step) hitting the
  nginx edge.
- Add `pytest`, `pytest-django`, `pytest-asyncio`, `httpx` to `requirements-dev.txt`;
  a GitHub Actions workflow to run them.

## Tasks

- [todo] inventory unit tests: concurrency (no oversell), idempotency replay, release
- [todo] order-service tests: idempotent create short-circuit, release compensation
- [todo] integration smoke via compose `--scale` hitting the nginx edge
- [todo] add test deps to `requirements-dev.txt`
- [todo] GitHub Actions CI workflow

## Changelog
- 2026-07-11 — drafted from the approved multi-replica plan (Phase 6), deferred after
  Phases 1–3 shipped in spec 009.
