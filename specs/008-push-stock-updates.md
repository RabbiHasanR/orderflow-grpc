---
id: 008
title: Push-based stock updates (LISTEN/NOTIFY)
service: inventory-service
status: in-progress
created: 2026-07-10
updated: 2026-07-10
related: [architecture.md, "decisions.md#d-039", "decisions.md#d-038", "specs/007-live-stock-watch-bidi.md", "inventory-service/grpc_server/servicer.py", "inventory-service/grpc_server/notifications.py"]
---

## Context / Why

The bidirectional `WatchStock` RPC ([007](007-live-stock-watch-bidi.md), D-038)
originally **polled** the DB every `GRPC_WATCH_POLL_SECONDS` (2s): each tick it
re-read every watched product and diffed against the last value, whether or not
anything had changed. That means constant idle queries and up-to-2s latency.

This capability makes it **push / event-driven** — the change-feed flagged as the
scaling fix in [D-038](../decisions.md#d-038). A Postgres `AFTER UPDATE` trigger
fires `pg_notify('stock_changed', …)` whenever a product's `available_quantity`
changes; each watch holds a `LISTEN` connection and sleeps until a notification
for a *watched* product arrives. No timer, no idle queries, near-instant updates.
The gRPC contract is unchanged — same `WatchCommand`/`StockUpdate`, same REST/WS
edge — only the server's update engine changes. See [D-039](../decisions.md#d-039).

## Requirements

- A Postgres trigger SHALL `NOTIFY` on channel `stock_changed` with a JSON
  payload `{product_id, available_quantity}` whenever
  `inventory_app_product.available_quantity` actually changes (no-op updates SHALL
  NOT notify).
- The trigger SHALL fire for **every** writer (the `reserve_stock` path, a future
  restock, a manual SQL `UPDATE`), i.e. it lives in the DB, not app code.
- `WatchStock` SHALL derive `CHANGED` updates from these notifications, NOT from a
  periodic poll; with a watch open and no stock changes, the server SHALL issue no
  periodic queries.
- Each watch SHALL hold its own `LISTEN` connection (per-call), separate from the
  Django ORM connection.
- The listener SHALL reconnect if its connection drops and, on reconnect, SHALL
  re-read the whole watch set (`resync`) so notifications missed while
  disconnected are recovered.
- Behaviour SHALL be unchanged from the consumer's view: `SNAPSHOT` on subscribe,
  `CHANGED` on change, silence for unwatched/unchanged products; teardown remains
  client-driven with no deadline.
- The proto contract and all order-service code (client, WebSocket bridge, demo)
  SHALL be unchanged.

## Design

- **Trigger** ([migration 0003](../inventory-service/inventory_app/migrations/0003_stock_change_notify.py)):
  `RunSQL` creates `notify_stock_change()` + an `AFTER UPDATE OF available_quantity`
  trigger with a `WHEN (OLD.available_quantity IS DISTINCT FROM NEW.available_quantity)`
  guard. Reversible (drops both). Runs via the entrypoint's `migrate`.
- **Listener helper** ([notifications.py](../inventory-service/grpc_server/notifications.py)):
  `listen_stock_changes(stop, tick)` opens a **raw psycopg3** autocommit
  connection (built from `settings.DATABASES["default"]` — kept off the Django ORM
  so LISTEN's long-lived blocking connection can't disturb thread-local ORM
  connections), `LISTEN stock_changed`, and yields `StockEvent`s: `resync` on each
  (re)connect, `changed(product_id)` per notification. Auto-reconnects with a
  backoff; `tick` bounds the `notifies` wait so `stop` is honoured.
- **Servicer** ([servicer.py](../inventory-service/grpc_server/servicer.py)):
  `WatchStock` keeps the reader thread + `watch_set`/lock/`stop`, drops the poll,
  and adds a **listener thread**. Three producers feed one `queue.Queue`; the
  generator drains it: `snapshot` → emit initial values for newly-added ids;
  `changed` → re-read that product, emit if it differs from `last_seen`; `resync`
  → re-read the whole watch set, emit drift. Reuses `fetch_stock`/`_to_row`
  ([services.py](../inventory-service/inventory_app/services.py)). On exit: set
  `stop`, join the listener so its connection closes promptly.
- **Setting** ([settings.py](../inventory-service/inventory_project/settings.py)):
  `GRPC_WATCH_POLL_SECONDS` → `GRPC_WATCH_TICK_SECONDS` (default 1s), now only a
  shutdown-responsiveness knob, not a data-refresh interval.
- **Why a DB channel, not in-memory:** 1 replica today, but the architecture
  targets 2+ (D-026) with round-robin, so the process that changes stock is often
  not the one holding the watch. Both replicas share `inventory-db`, so
  `LISTEN/NOTIFY` crosses the gap; an in-memory event can't. The trigger also makes
  the DB the source of truth (catches every writer).
- **gRPC/distributed-systems concept:** this is a **change data capture / pub-sub**
  pattern — the datastore emits change events and consumers react, replacing
  poll-based staleness with event-driven freshness. `LISTEN/NOTIFY` is Postgres's
  built-in lightweight message bus, so no Redis/Kafka is needed at this scale.

## Tasks

- [done] migration 0003: `notify_stock_change()` + `stock_changed_notify` trigger (reversible)
- [done] `notifications.py`: raw psycopg3 LISTEN helper with reconnect + resync
- [done] servicer: poll → event `queue.Queue`; listener thread; snapshot/changed/resync; teardown
- [done] settings: `GRPC_WATCH_TICK_SECONDS`
- [done] D-039 recorded (+ D-038 caveat updated to point at it)
- [done] end-to-end verify: trigger exists; CHANGED in ~33ms after reserve (vs 2s poll); manual `UPDATE` also pushes (~109ms); unsubscribe filters; clean teardown

## Follow-up (out of scope)

Per-call LISTEN uses one DB connection per watcher — fine at demo scale, bounded
by Postgres's connection limit. The scale-up is a **shared process-wide listener**
(one LISTEN connection + a watcher registry the dispatcher fans out to); a future
spec. See [D-039](../decisions.md#d-039).

## Changelog

- 2026-07-10 — created; replaced `WatchStock` polling with push via a Postgres
  `stock_changed` trigger + per-call `LISTEN` connection; new `notifications.py`
  helper; `GRPC_WATCH_POLL_SECONDS` → `GRPC_WATCH_TICK_SECONDS`.
