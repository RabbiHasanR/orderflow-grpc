---
id: 004
title: Order-service domain layout + Alembic migrations
service: order-service
status: in-progress
created: 2026-07-06
updated: 2026-07-06
related: [architecture.md, "002-order-creation.md", "decisions.md#d-035", "decisions.md#d-033", "decisions.md#d-030"]
---

## Context / Why

order-service grew as a layer split (all models in `models.py`, all schemas in
`schemas.py`, route + gRPC orchestration inline in `main.py`) with the schema
created at startup by `Base.metadata.create_all` (D-033). Two problems as it
evolves: (1) `create_all` can only *create missing* tables — it never `ALTER`s an
existing one, so the first column change silently drifts; (2) a layer split
scatters one feature across many sibling files. This spec restructures the service
into a domain-module layout (per the `fastapi-orm-lite-scaffold` convention) and
adopts Alembic. It does **not** change the public contract or the runtime model.

## Requirements

- The system SHALL keep SQLAlchemy 2.0 **async** + asyncpg (D-030) — no switch to
  SQLModel/sync.
- The system SHALL preserve the public contract unchanged: `POST /orders`,
  `GET /orders/{id}`, `GET /healthz` at the app root (D-031). The module router
  aggregator SHALL mount at root, not under `/api/v1`.
- The system SHALL organize code as: `app/core/` (config, database),
  `app/api/api_v1.py` (router aggregator), `app/modules/orders/`
  (`router`, `service`, `schemas`, `models`). The gRPC orchestration SHALL live in
  `OrderService`, not the router.
- The system SHALL manage order-db's schema with Alembic; `alembic upgrade head`
  SHALL run in the container entrypoint before the ASGI server starts.
- The system SHALL NOT create tables at app startup (`create_all` removed).

## Design

- **Layout.** `app/core/config.py` (was `app/config.py`), `app/core/database.py`
  (was `app/db.py`, `init_models` removed), `app/modules/orders/models.py|schemas.py`
  (moved), plus new `service.py` (`OrderService.create_order` / `get_order`),
  `router.py` (thin adapter; `get_inventory` reads `request.app.state.inventory`),
  and `app/api/api_v1.py` (includes the orders router at `prefix="/orders"`).
  `grpc_client/` and `generated/` are cross-cutting and stay put.
- **Alembic (async).** `alembic/env.py` sources the URL from `settings.database_url`
  and drives the sync migration routine through an async connection
  (`connection.run_sync(...)`) since the engine is asyncpg. `0001_initial` is a
  hand-written baseline mirroring the current models (no local DB to autogenerate
  against — same constraint as inventory's D-025); future revisions use
  `alembic revision --autogenerate`. Every module's models are imported in `env.py`
  so autogenerate sees them.
- **Migrate on deploy.** Entrypoint runs `alembic upgrade head` then `exec`s uvicorn
  — mirrors inventory's Django `migrate` step, keeps DDL out of the request path
  and out of the ASGI process. See [D-035](../decisions.md#d-035).

## Tasks

- [done] Move config/db → `app/core/`; models/schemas → `app/modules/orders/`
- [done] Extract `OrderService` (reserve-then-persist) + thin `router.py`
- [done] `app/api/api_v1.py` aggregator mounted at root; slim `main.py`
- [done] Alembic: `alembic.ini`, async `env.py`, `script.py.mako`, `0001_initial`
- [done] Remove startup `create_all`; run `alembic upgrade head` in entrypoint
- [done] Add `alembic` to `requirements.txt`
- [todo] Verify end-to-end: `docker compose up --build` → migration runs →
  `POST /orders` + `GET /orders/{id}` still work

## Changelog
- 2026-07-06 — created; restructured order-service into the domain-module layout and
  adopted Alembic (D-035, supersedes D-033). Async SQLAlchemy (D-030) and the public
  contract (D-031) preserved. Verification against a live stack is the last task.
