# Decisions Log

A running record of *why* key choices were made in OrderFlow gRPC, for future
reference. Newest decisions at the bottom of each section. This is a learning
project, so the reasoning matters more than the outcome.

> Where a decision diverges from `architecture.md`, that doc is the original
> sketch and this log is the current source of truth until the doc is updated.

---

## Process / sequencing

### D-001 — Build inventory-service before order-service
**Date:** 2026-06-24
**Decision:** Implement the Django gRPC **server** (inventory) before the FastAPI
gRPC **client** (order).
**Why:** Both depend on the shared proto contract, but a client is hard to
exercise without a server to call. Standing up the server first gives the client
something real to talk to and avoids building a throwaway stub server.

### D-002 — Proto contract is the unavoidable first step ("Phase 0")
**Date:** 2026-06-24
**Decision:** No matter which service we build first, `proto/order_inventory.proto`
must be defined and regenerated before service code that uses the stubs.
**Why:** The `*_pb2*` stubs are generated *from* the proto. The contract is the
thing both services bind to; it is owned by neither.

---

## Contract

### D-010 — Batch `ReserveStock` RPC (not per-item)
**Date:** 2026-06-24
**Decision:** One `ReserveStock` call carries all items of an order; the response
returns per-item results.
**Why:** Matches a real order (one logical unit), fewer network round-trips, and a
single place to map success/failure. Per-item RPCs would push partial-failure
coordination up into order-service.
**Alternatives:** Per-item RPC — rejected as chattier and harder to keep atomic.

---

## inventory-service (Django gRPC server)

### D-020 — Django used as ORM + migrations only
**Date:** 2026-06-24
**Decision:** No web server, no views, no DRF, no templates, no contrib apps. The
runtime is a standalone gRPC server that calls `django.setup()`. `INSTALLED_APPS`
contains only `inventory_app`.
**Why:** This service speaks gRPC, not HTTP. Pulling in auth/admin/sessions would
create tables and machinery we never use. Keeping it lean makes the "Django as a
library" idea explicit.

### D-021 — Sync gRPC server + plain Django ORM (not grpc.aio)
**Date:** 2026-06-24
**Decision:** Run a synchronous `grpcio` server backed by a `ThreadPoolExecutor`;
servicer methods are plain `def`; ORM, `transaction.atomic()` and
`select_for_update()` are used directly.
**Why:** Django's ORM is natively synchronous. An async (`grpc.aio`) server would
force `sync_to_async` wrappers that push DB work onto a threadpool *anyway* — so
async would add ceremony without removing threads. Client- and server-side async
are independent, so a sync server still serves the async order-service client
fine. Matching the runtime to the tools (sync ORM → sync server) is the lesson.
**Diverges from:** `architecture.md`, which sketches a `grpc.aio` server.
**Alternatives:** grpc.aio + sync_to_async (more ceremony, no benefit here);
django-grpc-framework (hides the mechanics we want to learn).

### D-022 — All-or-nothing batch reservation
**Date:** 2026-06-24
**Decision:** A reservation runs inside one `transaction.atomic()`. If any item
lacks stock, roll the whole batch back and return per-item results showing which
failed; nothing is reserved.
**Why:** Cleaner consistency story — an order reserves fully or not at all.
Partial commits would leave order-service to manage partial state and rollback.
**Alternatives:** Partial commit (shopping-cart feel) — rejected for v1.

### D-023 — Service-layer pattern, single app (not multi-app, not repository/DDD)
**Date:** 2026-06-24
**Decision:** One Django app (`inventory_app`). Business logic lives in
`services.py` (use-case/orchestration), the gRPC `servicer.py` is a thin transport
adapter (proto ↔ Python, error → status), and the ORM is the data-access layer.
Reads may later move to `selectors.py` if the service grows.
**Why:** A microservice is one bounded context, so feature-splitting into many
apps is monolith ceremony. The reservation use case spans two models in one
locked transaction — too much for a single model method, and it must not live in
the transport. A service layer keeps it framework-agnostic and unit-testable
without gRPC. Wrapping the ORM in repositories is needless indirection at this
scale (the ORM already *is* the repository).
**Alternatives:** App-based/feature split (monolith concern); repository/DDD over
the ORM (over-engineered).

### D-024 — `StockReservation.product` uses `on_delete=PROTECT`
**Date:** 2026-06-24
**Decision:** Foreign key from reservation to product protects against deletion.
**Why:** A product with live reservations should not be deletable — guards
inventory integrity.

### D-025 — Hand-written migrations
**Date:** 2026-06-24
**Decision:** `0001_initial` and `0002_seed_products` were hand-written to match
the models.
**Why:** Django is not installed locally (the service runs in Docker), so
`makemigrations` could not be run on the host. They are correct but unverified by
Django until first run — `python manage.py makemigrations --check --dry-run` in
the container will confirm zero drift. Prefer regenerating via `makemigrations`
once the container exists.

### D-026 — Single inventory replica for now (defer round-robin)
**Date:** 2026-06-24
**Decision:** Run one `inventory-service` replica initially; add a second (same
image) later to demonstrate client-side round-robin load balancing.
**Why:** Keeps the first pass simple. With one replica there is no concurrent
`migrate` race, so migrations can run in the container entrypoint and the
dedicated one-shot migrate service is not needed yet.
**Diverges from:** `architecture.md` (2 replicas + round-robin, project goal #3).
**Follow-up:** Re-introduce the 2nd replica + one-shot migrate service when
building the load-balancing demo.

### D-027 — Production-grade image: multi-stage, runtime-only deps, COPYed stubs
**Date:** 2026-06-30
**Decision:** Containerize inventory-service with a multi-stage Dockerfile that
installs runtime deps into a venv in a builder stage and copies only that venv +
app code into a slim non-root runtime. Split `requirements.txt` (runtime) from
`requirements-dev.txt` (adds `grpcio-tools`); the image installs runtime-only.
The `generated/` stubs are produced out-of-band by `regen_proto.sh` and COPYed
in, not generated during the build. Healthcheck is a Python TCP connect to the
gRPC port. Entrypoint runs `migrate` then `exec`s the server. See
[spec 003](specs/003-inventory-containerization.md).
**Why:** A runtime image should ship the minimum to *run*, not to *build* —
no protobuf compiler (`grpcio-tools`), no pip cache, no toolchain, no root.
`psycopg[binary]`/`grpcio` ship manylinux wheels, so no compiler is needed and
the image stays small without `libpq-dev`. The build context is
`./inventory-service`; `proto/` sits at the repo root *outside* it, and the
documented workflow already generates stubs per-service — so COPYing them is the
natural fit and avoids widening the context just to re-run protoc. `exec` makes
the server PID 1's successor so the existing SIGTERM graceful-drain works. The
server registers no gRPC health service, so a TCP probe is the honest
no-extra-binary liveness check.
**Alternatives:** Hermetic in-image stub generation (context = repo root +
grpcio-tools in builder) — rejected: heavier, contradicts per-service regen flow.
Single-stage slim image — works (wheels need no build deps) but still ships pip
cache and is less clean than copying a prebuilt venv.
**Scope:** inventory-service only; one replica (consistent with D-026). order-
service image + 2nd replica + one-shot migrate job are later phases.

---

## order-service (FastAPI gRPC client) — planned, not yet built

### D-030 — Async SQLAlchemy + asyncpg (not sync + psycopg)
**Date:** 2026-06-24
**Decision:** Use `AsyncSession` + asyncpg for order-db.
**Why:** order-service is the public edge and its whole value is a non-blocking
REST → gRPC hop. asyncpg is natively async, so the DB leg yields like the gRPC
leg (`grpc.aio`) — uniform and no threadpool. A sync driver would smuggle a
blocking call into an otherwise async handler. (Contrast D-021: inventory's ORM
is natively sync, so sync wins there — match the runtime to the tools.)

### D-031 — Endpoints: `POST /orders`, `GET /orders/{id}`, `GET /healthz`
**Date:** 2026-06-24
**Decision:** `POST /orders` is the core flow (validate → ReserveStock over gRPC →
persist → 201). `GET /orders/{id}` for read-back; `GET /healthz` for liveness.
**Why:** The read endpoint makes the service demonstrable without psql; health is
for compose `depends_on`. The read endpoint is optional for v1.

### D-032 — Defer client verification harness
**Date:** 2026-06-24
**Decision:** No throwaway stub server and no mock-based unit tests for the gRPC
client yet; verify end-to-end once inventory-service exists (consequence of D-001).

### D-033 — order-db schema via SQLAlchemy `create_all` (Alembic deferred)
**Date:** 2026-07-01
**Status:** Superseded by D-035 (2026-07-06) — Alembic now manages the schema.
**Decision:** order-service creates its tables at startup with
`Base.metadata.create_all` (run in the FastAPI lifespan). No Alembic for v1.
**Why:** The schema is two tables (`orders`, `order_items`) and the service is a
learning demo — a migration tool would be ceremony before there is any schema to
migrate. `create_all` is idempotent and needs no extra process/entrypoint step.
Alembic is the documented future path (parallel to inventory's Django migrations)
for when the schema evolves and needs versioned, reversible changes.
**Contrast:** inventory-service uses real Django migrations (D-025) because Django
gives them for free and it already seeds data (`0002_seed_products`).
**Alternatives:** Alembic now (premature); a migrate step in the entrypoint like
inventory's (nothing to run without a migration tool).

### D-034 — Shared gRPC auth metadata key, enforced server-side
**Date:** 2026-07-01
**Decision:** The auth token travels as call metadata under the key
`x-auth-token`. The order-service client attaches it via a client-side
interceptor; the inventory server verifies it via a server-side auth interceptor
and aborts `UNAUTHENTICATED` on mismatch. If `GRPC_AUTH_TOKEN` is unset on the
server, enforcement is skipped (dev fallback, matching the settings convention).
**Why:** Interceptors keep auth out of the servicer/handlers (architecture §6).
Metadata is gRPC's per-call header channel — the natural place for a bearer-style
token, checked once at the edge of the server. A single shared constant on each
side prevents the client attaching a header the server never reads.
**Why not fail-closed when the server token is unset:** the whole stack must run
locally without configuring a secret; once `GRPC_AUTH_TOKEN` is set (compose/prod)
it is enforced. `UNAUTHENTICATED` maps to HTTP 502 at the client (a server-config
fault, not the caller's) per workflow.md.
**Alternatives:** channel-level call credentials (heavier, TLS-oriented — out of
scope, no TLS in v1); no server-side check (token attached but meaningless).

### D-035 — Adopt Alembic + domain-module layout for order-service (supersedes D-033)
**Date:** 2026-07-06
**Decision:** Manage order-db's schema with **Alembic** (run `alembic upgrade head`
in the container entrypoint, mirroring inventory's Django `migrate`), and
restructure order-service from a layer split into a **domain-module layout**:
`app/core/` (config, database), `app/api/api_v1.py` (router aggregator),
`app/modules/orders/` (`router`, `service`, `schemas`, `models`). The gRPC
orchestration moves from `main.py` into `OrderService`.
**Why (Alembic):** `create_all` only ever *creates missing* tables — it cannot
`ALTER` an existing one, so the first column change silently no-ops and the schema
drifts. Alembic gives versioned, reversible, reviewable migrations — the same
discipline inventory already has. D-033 correctly called this the future path;
that future is now (the schema is about to evolve, and running the two services
with matching "migrate on deploy" stories is clearer).
**Why (layout):** Domain cohesion — a feature's contract, rules, wiring, and
tables live in one folder, so a change touches one place instead of five sibling
files. Routers become thin HTTP adapters; `OrderService` is unit-testable without
FastAPI. Structure follows the `fastapi-orm-lite-scaffold` convention.
**Kept from before:** SQLAlchemy 2.0 **async** + asyncpg (D-030) — the scaffold's
SQLModel/sync default was explicitly *not* adopted; it would undo the non-blocking
REST→gRPC hop that is the service's whole point. The public contract is unchanged:
routers mount at the app root, so `POST /orders` / `GET /orders/{id}` (D-031) still
resolve — the aggregator is not under `/api/v1`.
**Migration runs in the entrypoint** (not app startup): schema reconciliation is a
deploy step, not request-path work, and it keeps the ASGI process from owning DDL.
**Alternatives:** Switch to SQLModel + sync (rejected — undoes D-030 for no gain);
keep `create_all` alongside Alembic (rejected — two sources of schema truth);
migrate manually only (rejected — a fresh `compose up` would boot with no tables).

### D-036 — Client-streaming `ReserveStockBulk` is best-effort per item
**Date:** 2026-07-08
**Decision:** Add a **client-streaming** RPC `ReserveStockBulk(stream
BulkReserveItem) → BulkReserveSummary` alongside — not replacing — the unary
`ReserveStock`. It reserves lines across **many orders** in one stream,
**best-effort per item**: the server reserves each streamed line in its own
transaction (reusing `reserve_stock` with a one-item list), so some lines succeed
while others fail and nothing rolls back the batch. It returns a single aggregate
summary at end-of-stream. order-service exposes it at `POST /orders/bulk`, which
persists only orders whose every line reserved. See [spec 005](specs/005-bulk-reserve-stream.md).
**Why client streaming:** the unary path already models "one order, many items,
all-or-nothing." The distinct `many-in → one-out` shape — a stream of lines
spanning many orders, folded into one summary — is exactly what client streaming
is for: it amortizes N reservations over one call/connection and lets the server
aggregate, versus N unary round-trips. Keeping it a *second* RPC leaves the unary
contract and its atomic guarantee untouched.
**Why best-effort (not atomic):** atomic-across-many-orders would make one bad
line fail an entire import — the wrong semantics for a bulk edge. Reusing
`reserve_stock` per line gives independent transactions for free and keeps the
row-lock concurrency safety of D-001/spec 001.
**Known caveat — orphaned reservations:** because reservation is per-item but
order persistence is per-order (all-or-nothing at persist time), a **partially**
reserved order leaves stock decremented on inventory while the order is *not*
saved on order-service. v1 surfaces these as `partial` in the response body; a
compensating release (saga-style) is deliberately out of scope for the learning
milestone (future spec/task).
**Alternatives:** atomic bulk (rejected — one bad line kills the batch); a new
best-effort service function on the server (rejected — `reserve_stock` per line
already is best-effort and is already tested); bidirectional streaming with a
per-item reply (rejected — caller only needs the aggregate, not a live per-line
ack).

### D-037 — Server-streaming `WatchLowStock`, re-streamed over REST as NDJSON
**Date:** 2026-07-09
**Decision:** Add a **server-streaming** RPC `WatchLowStock(LowStockQuery) →
stream ProductStock` — the third and final basic gRPC shape (`one-in →
many-out`), alongside unary `ReserveStock` and client-streaming
`ReserveStockBulk`. It is **read-only**: stream every product at/below a stock
threshold (optional `sku_prefix` filter), one message at a time. The server
yields lazily from the DB cursor (`.iterator()`) and stops early on
`context.is_active()`; an empty result is a valid zero-message stream.
order-service fronts it at `GET /inventory/low-stock` and **re-streams NDJSON**
(`application/x-ndjson`). See [spec 006](specs/006-watch-low-stock-stream.md).
**Why server streaming:** the point of the shape is a single request driving many
responses with lazy production and incremental consumption — constant memory
end-to-end. A low-stock/replenishment query is a natural fit (a catalog can be
large) and, unlike a "list" that buffers everything into one response, streaming
lets the first rows arrive before the last are read.
**Why NDJSON (not a buffered JSON array):** collapsing the stream into one array
would throw away the property being demonstrated — the whole result would sit in
memory on the order-service before the client sees a byte. NDJSON via FastAPI
`StreamingResponse` keeps the stream honest across the REST hop.
**Error handling splits in two (defining trait of a streamed response):** a
failure *before* the first byte still maps to an HTTP status — the edge **probes
the first message** inside `try/except` before returning the response; once bytes
are on the wire the status line is sent, so a mid-stream failure can only *end*
the stream (logged, not re-mapped). The per-call deadline bounds the **whole**
stream — fine for this bounded query, but a true long-lived "watch" would drop or
extend it.
**Interceptors (third kind):** both layers were per-kind. Server-side the auth
abort-handler and logging wrapper gained a `handler.unary_stream` branch; logging
needed a **stream-aware** wrapper because a streaming handler returns lazily
(latency/count known only at drain). Client-side a third `UnaryStreamAuthInterceptor`
was registered (grpc.aio buckets interceptors by kind).
**Also (DRY):** the gRPC→HTTP status map moved to `grpc_client/errors.py` and the
`get_inventory` FastAPI dependency to `grpc_client/deps.py`, now shared by the
orders and inventory routers.
**Alternatives:** buffered JSON array (rejected — loses the streaming property);
adding low-stock to the orders module (rejected — it is an inventory read, not an
order write; a dedicated read-only module fits the domain-module layout, D-035);
a long-lived push "watch" via server-side polling (rejected for v1 — the bounded
snapshot query teaches the shape without a change-feed).

### D-038 — Bidirectional-streaming `WatchStock`, fronted over a WebSocket
**Date:** 2026-07-10
**Decision:** Add the fourth and final gRPC shape — **bidirectional streaming**
`WatchStock(stream WatchCommand) → stream StockUpdate`. The client streams
subscribe/unsubscribe commands; the server independently streams `StockUpdate`s
for the current watch set — a `SNAPSHOT` when a product is subscribed, then a
`CHANGED` whenever a watched product's `available_quantity` differs on a poll tick
(`GRPC_WATCH_POLL_SECONDS`, default 2s). Read-only; the reservation write-path is
untouched. order-service fronts it at a **WebSocket** `WS /inventory/stock-watch`
plus a browser demo page `GET /inventory/stock-watch/demo`. See
[spec 007](specs/007-live-stock-watch-bidi.md).
**Why bidi (vs the other three shapes):** the defining property is *decoupled
duplex* — the request and response streams flow concurrently and a response is
**not** 1:1 with a request. A subscribe command doesn't yield "one answer"; it
reshapes what the response stream emits. This is a clean evolution of
server-streaming `WatchLowStock` (D-037): a fixed one-shot query becomes a
*mutable live subscription* you steer mid-stream without reconnecting.
**Why a WebSocket edge (not GET/NDJSON):** NDJSON fronted `WatchLowStock` because
that stream was one-directional. A plain HTTP request body cannot carry a
*client→server* message stream, so the honest full-duplex edge is a WebSocket.
The edge runs **two concurrent asyncio tasks** (socket→gRPC and gRPC→socket),
mirroring the server's two threads; whichever side ends first tears the other
down. A demo HTML page makes the duplex visible in a browser.
**Sync-server concurrency (the core lesson):** on the ThreadPoolExecutor server
(D-021) one thread cannot both block on `for cmd in request_iterator` and
poll+`yield` responses, so the handler spawns a **background reader thread** that
drains the command stream into a lock-guarded `watch_set`, while the handler's own
pool thread polls and yields. Teardown is **client-driven** — there is no deadline
on a live watch (the client omits the per-call `GRPC_DEADLINE_SECONDS` that the
other RPCs use, the concrete form of the D-037 "a real watch would drop/extend the
deadline" caveat); the loop exits when the request stream closes (`stop` event) or
the client cancels (`context.is_active()`).
**Interceptors (fourth kind):** both layers were per-RPC-kind. Server-side the
auth abort-handler and the logging wrapper gained a `handler.stream_stream` branch
(logging reuses the existing `timed_stream` wrapper — it already iterates a
response generator regardless of request arity). Client-side a fourth
`StreamStreamAuthInterceptor` was registered (grpc.aio buckets interceptors by
kind).
**Scaling trade-off (accepted for a learning/demo scale):** each active watch
holds **two** pool threads (handler + reader) for its whole lifetime, bounded by
`GRPC_MAX_WORKERS`, and `server.stop(grace)` will not force-close long-lived
streams (teardown is client-driven). A production system at scale would use an
async server and/or a DB change-feed (LISTEN/NOTIFY) instead of per-watcher
polling — deliberately out of scope here. **Update:** the polling half of this
was resolved in [D-039](#d-039) (push via LISTEN/NOTIFY); the two-threads-per-watch
and sync-server points still stand.
**Alternatives:** interactive reservation-session bidi (rejected — "ping-pong",
one response per request, barely needs full-duplex and doesn't show stream
independence); SSE + POST hybrid at the edge (rejected — two half-duplex channels
to emulate what one WebSocket does honestly); gRPC-only with no HTTP edge
(rejected — the WebSocket bridge is itself a realistic, testable production
pattern worth demonstrating).

### D-039 — Push-based `WatchStock` via Postgres LISTEN/NOTIFY (replaces polling)
**Date:** 2026-07-10
**Decision:** Replace `WatchStock`'s 2s DB poll with a **push / event-driven**
change-feed. A Postgres `AFTER UPDATE OF available_quantity` trigger on
`inventory_app_product` fires `pg_notify('stock_changed', '{product_id,
available_quantity}')`; each `WatchStock` call holds its own **`LISTEN`
connection** (per-call) and sleeps until a notification for a *watched* product
arrives, then emits `CHANGED`. The gRPC contract and all order-service code
(client, WebSocket bridge, demo) are **unchanged** — only the server's update
engine changes. See [spec 008](specs/008-push-stock-updates.md).
**Why push over poll:** polling issued idle queries every 2s and added up-to-2s
latency; push does zero work while nothing changes and delivers near-instantly.
This is the change-feed flagged as the fix in [D-038](#d-038).
**Why a DB trigger (not app-level `pg_notify` in `reserve_stock`):** the trigger
fires for **every** writer — the reserve path, a future restock, even a manual
SQL `UPDATE` — so the DB stays the source of truth and no code path can forget to
notify. Cost: some logic lives in SQL (a `RunSQL` migration).
**Why a DB channel (not an in-memory Python event bus):** 1 replica today, but the
architecture targets 2+ (D-026) with round-robin, so the process that changes
stock is often not the one holding the watch stream. Both replicas share
`inventory-db`, so `LISTEN/NOTIFY` crosses that gap; an in-memory event can't.
No Redis/Kafka needed — `LISTEN/NOTIFY` is Postgres's built-in lightweight bus.
**Why per-call LISTEN (not a shared process-wide listener):** simplest first cut,
mirrors the existing per-call reader thread, one new concept at a time. Cost: one
DB connection per watcher, bounded by Postgres's connection limit — fine at demo
scale. The shared-listener + registry is the documented scale-up (future spec).
**Concurrency:** the sync handler keeps the reader thread + `watch_set`/lock and
adds a **listener thread**; three producers feed one `queue.Queue` the generator
drains (`snapshot`/`changed`/`resync`). The listener uses a **raw psycopg3**
autocommit connection (off the Django ORM, so LISTEN's long-lived blocking
connection can't disturb thread-local ORM connections). `GRPC_WATCH_POLL_SECONDS`
became `GRPC_WATCH_TICK_SECONDS` — now only a shutdown-responsiveness knob.
**Correctness backstop:** NOTIFY is fire-and-forget, so a notification sent while
the listener is reconnecting would be lost. On every (re)connect the listener
emits a `resync` that re-reads the whole watch set and pushes any drift — this
recovers missed changes and replaces the poll's implicit self-healing.
**Alternatives:** keep polling (rejected — the whole point was to remove it);
app-level `pg_notify` (rejected — misses non-`reserve_stock` writers); in-memory
bus (rejected — doesn't survive multi-replica); Redis/Kafka (rejected — real
change-feed infra, unjustified at this scale when Postgres already has the bus).
